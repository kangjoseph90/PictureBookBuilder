"""
XML Exporter - Adobe Premiere Pro 호환 XML 프로젝트 파일 생성기

프리미어 프로에서 가져오기(Import) 가능한 XMEML(Final Cut Pro XML) 형식을 생성합니다.
이 형식은 프리미어 프로와 파이널 컷 프로 모두에서 지원됩니다.

주요 기능:
- 오디오/비디오 클립을 타임라인에 배치
- 한글 경로 지원 (URI 인코딩)
- 소스 파일의 특정 구간만 사용 가능 (source_in/source_out)
"""
import re
import uuid
import urllib.parse
from pathlib import Path
from dataclasses import dataclass
from xml.etree import ElementTree as ET
from xml.dom import minidom

from config import VIDEO_FPS


@dataclass
class TimelineClip:
    """타임라인에 배치될 클립 정보
    
    Attributes:
        name: 클립 이름 (프리미어에서 표시됨)
        file_path: 소스 파일 경로
        start_time: 타임라인에서의 시작 위치 (초)
        end_time: 타임라인에서의 종료 위치 (초)
        track: 트랙 번호 (1부터 시작)
        clip_type: 클립 유형 ("audio" 또는 "video")
        source_in: 소스 파일에서 시작 지점 (초)
        source_out: 소스 파일에서 종료 지점 (초, None이면 클립 길이 사용)
    """
    name: str
    file_path: str
    start_time: float
    end_time: float
    track: int = 1
    clip_type: str = "audio"
    source_in: float = 0.0
    source_out: float = None


class XMLExporter:
    """프리미어 프로 호환 XML(XMEML) 내보내기 클래스
    
    프리미어 프로에서 정상적으로 불러올 수 있는 XML 파일을 생성합니다.
    파일 경로 인코딩, 트랙 속성, 클립 메타데이터 등 프리미어 프로의 요구사항을 충족합니다.
    """
    
    # 프리미어 프로 시퀀스 기본 속성
    _SEQUENCE_ATTRIBS = {
        "TL.SQAudioVisibleBase": "0",
        "TL.SQVideoVisibleBase": "0",
        "TL.SQVisibleBaseTime": "0",
        "TL.SQAVDividerPosition": "0.5",
        "TL.SQHideShyTracks": "0",
        "TL.SQHeaderWidth": "204",
        "TL.SQDataTrackViewControlState": "1",
        "Monitor.ProgramZoomOut": "120047231304000",
        "Monitor.ProgramZoomIn": "0",
        "TL.SQTimePerPixel": "0.063532553962619323",
        "MZ.EditLine": "0",
        "MZ.Sequence.PreviewFrameSizeHeight": "1080",
        "MZ.Sequence.PreviewFrameSizeWidth": "1920",
        "MZ.Sequence.AudioTimeDisplayFormat": "200",
        "MZ.Sequence.PreviewRenderingClassID": "1061109567",
        "MZ.Sequence.PreviewRenderingPresetCodec": "1634755443",
        "MZ.Sequence.PreviewRenderingPresetPath": "EncoderPresets\\SequencePreview\\9678af98-a7b7-4bdb-b477-7ac9c8df4a4e\\QuickTime.epr",
        "MZ.Sequence.PreviewUseMaxRenderQuality": "false",
        "MZ.Sequence.PreviewUseMaxBitDepth": "false",
        "MZ.Sequence.EditingModeGUID": "9678af98-a7b7-4bdb-b477-7ac9c8df4a4e",
        "MZ.Sequence.VideoTimeDisplayFormat": "110",
        "MZ.WorkOutPoint": "15235011792000",
        "MZ.WorkInPoint": "0",
        "explodedTracks": "true"
    }
    
    # 비디오 트랙 기본 속성
    _VIDEO_TRACK_ATTRIBS = {
        "TL.SQTrackShy": "0",
        "TL.SQTrackExpandedHeight": "41",
        "TL.SQTrackExpanded": "0",
    }
    
    # 오디오 트랙 기본 속성
    _AUDIO_TRACK_ATTRIBS = {
        "TL.SQTrackAudioKeyframeStyle": "0",
        "TL.SQTrackShy": "0",
        "TL.SQTrackExpandedHeight": "41",
        "TL.SQTrackExpanded": "0",
        "PannerCurrentValue": "0.5",
        "PannerIsInverted": "true",
        "PannerStartKeyframe": "-91445760000000000,0.5,0,0,0,0,0,0",
        "PannerName": "균형",
        "currentExplodedTrackIndex": "0",
        "totalExplodedTrackCount": "1",
        "premiereTrackType": "Stereo"
    }
    
    def __init__(self, fps: int = VIDEO_FPS, ntsc: bool = True):
        """
        Args:
            fps: 시퀀스 프레임 레이트 (기본값: config.VIDEO_FPS)
            ntsc: NTSC 모드 여부 (True면 드롭프레임 타임코드 사용)
        """
        self.fps = fps
        self.ntsc = ntsc
        self._file_id_counter = 0
        self._clipitem_id_counter = 0
        self._masterclip_id_counter = 0
        self._file_registry: dict[str, str] = {}
        self._masterclip_registry: dict[str, str] = {}
    
    def _make_premiere_pathurl(self, file_path: str) -> str:
        """파일 경로를 프리미어 프로 호환 URL 형식으로 변환
        
        프리미어 프로는 다음과 같은 URI 인코딩을 요구합니다:
        - 한글: UTF-8 hex 인코딩 (%eb%85%b8 등)
        - 공백: %20
        - 콜론: %3a (드라이브 문자 포함)
        - 소문자 hex 코드 사용 (%3A가 아닌 %3a)
        """
        normalized_path = file_path.replace("\\", "/")
        encoded_path = urllib.parse.quote(normalized_path, safe='/')
        
        # 프리미어 프로 호환을 위해 소문자로 변환
        encoded_path = re.sub(
            r'%([0-9A-Fa-f]{2})',
            lambda m: f'%{m.group(1).lower()}',
            encoded_path
        )
        
        return f"file://localhost/{encoded_path}"
    
    def _reset_counters(self):
        """새 내보내기를 위해 ID 카운터 초기화"""
        self._file_id_counter = 0
        self._clipitem_id_counter = 0
        self._masterclip_id_counter = 0
        self._file_registry.clear()
        self._masterclip_registry.clear()
    
    def _get_file_id(self, file_path: str) -> tuple[str, bool]:
        """파일 경로에 대한 고유 ID 반환. (file_id, is_new) 튜플 반환."""
        if file_path in self._file_registry:
            return self._file_registry[file_path], False
        
        self._file_id_counter += 1
        file_id = f"file-{self._file_id_counter}"
        self._file_registry[file_path] = file_id
        return file_id, True
    
    def _get_masterclip_id(self, file_path: str) -> str:
        """파일 경로에 대한 마스터클립 ID 반환"""
        if file_path not in self._masterclip_registry:
            self._masterclip_id_counter += 1
            self._masterclip_registry[file_path] = f"masterclip-{self._masterclip_id_counter}"
        return self._masterclip_registry[file_path]
    
    def _get_clipitem_id(self) -> str:
        """다음 클립아이템 ID 반환"""
        self._clipitem_id_counter += 1
        return f"clipitem-{self._clipitem_id_counter}"
    
    def seconds_to_frames(self, seconds: float) -> int:
        """초를 프레임 수로 변환"""
        return int(seconds * self.fps)
    
    def _add_rate_element(self, parent: ET.Element, timebase: int = None) -> ET.Element:
        """rate 요소 추가 (timebase와 ntsc 포함)"""
        rate = ET.SubElement(parent, 'rate')
        ET.SubElement(rate, 'timebase').text = str(timebase if timebase else self.fps)
        ET.SubElement(rate, 'ntsc').text = 'TRUE' if self.ntsc else 'FALSE'
        return rate
    
    def _add_timecode_element(self, parent: ET.Element, timebase: int = None) -> ET.Element:
        """timecode 요소 추가"""
        timecode = ET.SubElement(parent, 'timecode')
        self._add_rate_element(timecode, timebase)
        ET.SubElement(timecode, 'string').text = '00;00;00;00' if self.ntsc else '00:00:00:00'
        ET.SubElement(timecode, 'frame').text = '0'
        ET.SubElement(timecode, 'displayformat').text = 'DF' if self.ntsc else 'NDF'
        return timecode

    def create_xmeml(
        self,
        clips: list[TimelineClip],
        project_name: str = "PictureBookProject",
        sequence_name: str = "Main Sequence"
    ) -> ET.Element:
        """프리미어 프로 호환 XMEML 구조 생성
        
        Args:
            clips: TimelineClip 객체 리스트
            project_name: 프로젝트 이름
            sequence_name: 시퀀스 이름
            
        Returns:
            XMEML 루트 요소
        """
        self._reset_counters()
        
        # 루트 요소
        xmeml = ET.Element('xmeml', version="4")
        
        # 시퀀스 요소 (프리미어 프로 전용 속성 포함)
        sequence_attribs = {"id": "sequence-1", **self._SEQUENCE_ATTRIBS}
        sequence = ET.SubElement(xmeml, 'sequence', **sequence_attribs)
        
        ET.SubElement(sequence, 'uuid').text = str(uuid.uuid4())
        ET.SubElement(sequence, 'duration').text = str(self._get_total_duration(clips))
        self._add_rate_element(sequence)
        ET.SubElement(sequence, 'name').text = sequence_name
        
        # 미디어 컨테이너
        media = ET.SubElement(sequence, 'media')
        
        # 비디오 섹션
        video = ET.SubElement(media, 'video')
        self._add_video_format(video)
        video_clips = [c for c in clips if c.clip_type == 'video']
        if video_clips:
            self._add_video_tracks(video, video_clips)
        else:
            self._add_empty_video_track(video)
        
        # 오디오 섹션
        audio = ET.SubElement(media, 'audio')
        self._add_audio_format(audio)
        audio_clips = [c for c in clips if c.clip_type == 'audio']
        if audio_clips:
            self._add_audio_tracks(audio, audio_clips)
        else:
            self._add_empty_audio_track(audio)
        
        return xmeml
    
    def _add_empty_video_track(self, video_elem: ET.Element):
        """빈 비디오 트랙 추가"""
        track_attribs = {**self._VIDEO_TRACK_ATTRIBS, "MZ.TrackTargeted": "1"}
        track = ET.SubElement(video_elem, 'track', **track_attribs)
        ET.SubElement(track, 'enabled').text = 'TRUE'
        ET.SubElement(track, 'locked').text = 'FALSE'
    
    def _add_empty_audio_track(self, audio_elem: ET.Element):
        """빈 오디오 트랙 추가"""
        track_attribs = {**self._AUDIO_TRACK_ATTRIBS, "MZ.TrackTargeted": "1"}
        track = ET.SubElement(audio_elem, 'track', **track_attribs)
        ET.SubElement(track, 'enabled').text = 'TRUE'
        ET.SubElement(track, 'locked').text = 'FALSE'
    
    def _add_video_format(self, video_elem: ET.Element):
        """비디오 포맷 정보 추가 (1920x1080, ProRes 422)"""
        format_elem = ET.SubElement(video_elem, 'format')
        sample = ET.SubElement(format_elem, 'samplecharacteristics')
        self._add_rate_element(sample)
        
        # 코덱 정보
        codec = ET.SubElement(sample, 'codec')
        ET.SubElement(codec, 'name').text = 'Apple ProRes 422'
        appdata = ET.SubElement(codec, 'appspecificdata')
        ET.SubElement(appdata, 'appname').text = 'Final Cut Pro'
        ET.SubElement(appdata, 'appmanufacturer').text = 'Apple Inc.'
        ET.SubElement(appdata, 'appversion').text = '7.0'
        data = ET.SubElement(appdata, 'data')
        qtcodec = ET.SubElement(data, 'qtcodec')
        ET.SubElement(qtcodec, 'codecname').text = 'Apple ProRes 422'
        ET.SubElement(qtcodec, 'codectypename').text = 'Apple ProRes 422'
        ET.SubElement(qtcodec, 'codectypecode').text = 'apcn'
        ET.SubElement(qtcodec, 'codecvendorcode').text = 'appl'
        ET.SubElement(qtcodec, 'spatialquality').text = '1024'
        ET.SubElement(qtcodec, 'temporalquality').text = '0'
        ET.SubElement(qtcodec, 'keyframerate').text = '0'
        ET.SubElement(qtcodec, 'datarate').text = '0'
        
        # 해상도
        ET.SubElement(sample, 'width').text = '1920'
        ET.SubElement(sample, 'height').text = '1080'
        ET.SubElement(sample, 'anamorphic').text = 'FALSE'
        ET.SubElement(sample, 'pixelaspectratio').text = 'square'
        ET.SubElement(sample, 'fielddominance').text = 'none'
        ET.SubElement(sample, 'colordepth').text = '24'
    
    def _add_audio_format(self, audio_elem: ET.Element):
        """오디오 포맷 정보 추가 (48kHz, 16bit, Stereo)"""
        ET.SubElement(audio_elem, 'numOutputChannels').text = '2'
        format_elem = ET.SubElement(audio_elem, 'format')
        sample = ET.SubElement(format_elem, 'samplecharacteristics')
        ET.SubElement(sample, 'depth').text = '16'
        ET.SubElement(sample, 'samplerate').text = '48000'
        
        # 출력 채널
        outputs = ET.SubElement(audio_elem, 'outputs')
        for i in range(1, 3):
            group = ET.SubElement(outputs, 'group')
            ET.SubElement(group, 'index').text = str(i)
            ET.SubElement(group, 'numchannels').text = '1'
            ET.SubElement(group, 'downmix').text = '0'
            channel = ET.SubElement(group, 'channel')
            ET.SubElement(channel, 'index').text = str(i)
    
    def _get_total_duration(self, clips: list[TimelineClip]) -> int:
        """전체 타임라인 길이를 프레임 수로 반환"""
        if not clips:
            return 0
        max_end = max(c.end_time for c in clips)
        return self.seconds_to_frames(max_end)
    
    def _add_video_tracks(self, video_elem: ET.Element, clips: list[TimelineClip]):
        """비디오 트랙에 클립 추가"""
        tracks: dict[int, list[TimelineClip]] = {}
        for clip in clips:
            tracks.setdefault(clip.track, []).append(clip)
        
        for track_num in sorted(tracks.keys()):
            sorted_clips = sorted(tracks[track_num], key=lambda c: c.start_time)
            track_attribs = {
                **self._VIDEO_TRACK_ATTRIBS,
                "MZ.TrackTargeted": "1" if track_num == 1 else "0"
            }
            track = ET.SubElement(video_elem, 'track', **track_attribs)
            
            for clip in sorted_clips:
                self._add_clip_item(track, clip, 'video')
            
            # enabled/locked는 clipitem 뒤에 와야 함 (프리미어 요구사항)
            ET.SubElement(track, 'enabled').text = 'TRUE'
            ET.SubElement(track, 'locked').text = 'FALSE'
    
    def _add_audio_tracks(self, audio_elem: ET.Element, clips: list[TimelineClip]):
        """오디오 트랙에 클립 추가"""
        tracks: dict[int, list[TimelineClip]] = {}
        for clip in clips:
            tracks.setdefault(clip.track, []).append(clip)
        
        for track_num in sorted(tracks.keys()):
            sorted_clips = sorted(tracks[track_num], key=lambda c: c.start_time)
            track_attribs = {
                **self._AUDIO_TRACK_ATTRIBS,
                "MZ.TrackTargeted": "1" if track_num == 1 else "0"
            }
            track = ET.SubElement(audio_elem, 'track', **track_attribs)
            
            for clip in sorted_clips:
                self._add_clip_item(track, clip, 'audio')
            
            # enabled/locked는 clipitem 뒤에 와야 함 (프리미어 요구사항)
            ET.SubElement(track, 'enabled').text = 'TRUE'
            ET.SubElement(track, 'locked').text = 'FALSE'
    
    def _add_clip_item(self, track: ET.Element, clip: TimelineClip, media_type: str):
        """트랙에 클립아이템 추가
        
        Args:
            track: 부모 트랙 요소
            clip: TimelineClip 객체
            media_type: 'audio' 또는 'video'
        """
        clipitem_id = self._get_clipitem_id()
        
        # 오디오 클립은 mono 채널 타입 지정
        if media_type == 'audio':
            clipitem = ET.SubElement(track, 'clipitem', id=clipitem_id, premiereChannelType="mono")
        else:
            clipitem = ET.SubElement(track, 'clipitem', id=clipitem_id)
        
        # 기본 정보
        ET.SubElement(clipitem, 'masterclipid').text = self._get_masterclip_id(clip.file_path)
        ET.SubElement(clipitem, 'name').text = clip.name
        ET.SubElement(clipitem, 'enabled').text = 'TRUE'
        
        # 길이 정보
        clip_duration = clip.end_time - clip.start_time
        ET.SubElement(clipitem, 'duration').text = str(self.seconds_to_frames(clip_duration))
        self._add_rate_element(clipitem)
        
        # 타임라인 위치 (start/end)
        ET.SubElement(clipitem, 'start').text = str(self.seconds_to_frames(clip.start_time))
        ET.SubElement(clipitem, 'end').text = str(self.seconds_to_frames(clip.end_time))
        
        # 소스 파일 구간 (in/out) - 소스 파일의 어느 부분을 재생할지 지정
        source_in = clip.source_in if clip.source_in else 0
        source_out = clip.source_out if clip.source_out else clip_duration
        ET.SubElement(clipitem, 'in').text = str(self.seconds_to_frames(source_in))
        ET.SubElement(clipitem, 'out').text = str(self.seconds_to_frames(source_out))
        
        # 파일 참조
        self._add_file_reference(clipitem, clip, media_type, clip_duration)
        
        # 소스 트랙 정보
        sourcetrack = ET.SubElement(clipitem, 'sourcetrack')
        ET.SubElement(sourcetrack, 'mediatype').text = media_type
        ET.SubElement(sourcetrack, 'trackindex').text = '1'
        
        # 메타데이터 (빈 값이지만 필수)
        self._add_clip_metadata(clipitem)
    
    def _add_file_reference(self, clipitem: ET.Element, clip: TimelineClip, 
                           media_type: str, clip_duration: float):
        """파일 참조 요소 추가
        
        같은 파일을 여러 번 사용할 경우, 첫 번째만 전체 정보를 포함하고
        이후에는 ID 참조만 사용합니다.
        """
        file_id, is_new_file = self._get_file_id(clip.file_path)
        
        if is_new_file:
            file_elem = ET.SubElement(clipitem, 'file', id=file_id)
            ET.SubElement(file_elem, 'name').text = Path(clip.file_path).name
            ET.SubElement(file_elem, 'pathurl').text = self._make_premiere_pathurl(clip.file_path)
            
            # 오디오 파일은 30fps timebase 사용 (프리미어 표준)
            file_timebase = 30 if media_type == 'audio' else self.fps
            self._add_rate_element(file_elem, file_timebase)
            ET.SubElement(file_elem, 'duration').text = str(int(clip_duration * file_timebase))
            self._add_timecode_element(file_elem, file_timebase)
            
            # 미디어 정보
            file_media = ET.SubElement(file_elem, 'media')
            if media_type == 'audio':
                audio = ET.SubElement(file_media, 'audio')
                sample = ET.SubElement(audio, 'samplecharacteristics')
                ET.SubElement(sample, 'depth').text = '16'
                ET.SubElement(sample, 'samplerate').text = '48000'
                ET.SubElement(audio, 'channelcount').text = '1'
                audiochannel = ET.SubElement(audio, 'audiochannel')
                ET.SubElement(audiochannel, 'sourcechannel').text = '1'
            else:
                video = ET.SubElement(file_media, 'video')
                sample = ET.SubElement(video, 'samplecharacteristics')
                self._add_rate_element(sample)
                ET.SubElement(sample, 'width').text = '1920'
                ET.SubElement(sample, 'height').text = '1080'
        else:
            # 이후 참조는 ID만 사용
            ET.SubElement(clipitem, 'file', id=file_id)
    
    def _add_clip_metadata(self, clipitem: ET.Element):
        """클립 메타데이터 추가 (빈 값이지만 프리미어 호환성을 위해 필수)"""
        logginginfo = ET.SubElement(clipitem, 'logginginfo')
        for tag in ['description', 'scene', 'shottake', 'lognote', 'good', 
                    'originalvideofilename', 'originalaudiofilename']:
            ET.SubElement(logginginfo, tag)
        
        colorinfo = ET.SubElement(clipitem, 'colorinfo')
        for tag in ['lut', 'lut1', 'asc_sop', 'asc_sat', 'lut2']:
            ET.SubElement(colorinfo, tag)
        
        labels = ET.SubElement(clipitem, 'labels')
        ET.SubElement(labels, 'label2').text = 'Caribbean'
    
    def to_string(self, xmeml: ET.Element) -> str:
        """XML 요소를 문자열로 변환 (UTF-8 인코딩 선언 및 DOCTYPE 포함)"""
        rough_string = ET.tostring(xmeml, encoding='unicode')
        reparsed = minidom.parseString(rough_string)
        xml_output = reparsed.toprettyxml(indent="\t")
        
        # XML 선언 및 DOCTYPE 추가
        lines = xml_output.split('\n')
        if lines[0].startswith('<?xml'):
            lines[0] = '<?xml version="1.0" encoding="UTF-8"?>'
            lines.insert(1, '<!DOCTYPE xmeml>')
        
        return '\n'.join(lines)
    
    def save(
        self,
        clips: list[TimelineClip],
        output_path: str | Path,
        project_name: str = "PictureBookProject",
        sequence_name: str = "Main Sequence"
    ) -> None:
        """타임라인을 XML 파일로 저장
        
        Args:
            clips: TimelineClip 객체 리스트
            output_path: 출력 파일 경로
            project_name: 프로젝트 이름
            sequence_name: 시퀀스 이름
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        xmeml = self.create_xmeml(clips, project_name, sequence_name)
        xml_string = self.to_string(xmeml)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(xml_string)
