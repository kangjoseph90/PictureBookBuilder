# Subtitle Segmentation

## Problem

Long dialogue lines must be split into readable subtitle segments. A naive approach (fixed character count) produces awkward breaks:

```
"흥부는 마음이 착하고 | 부지런했지만 놀부는"  ← Bad: splits mid-clause
"흥부는 마음이 착하고 부지런했지만 | 놀부는"  ← Good: splits at conjunction
```

The challenge: find linguistically natural break points while respecting length constraints.

---

## Solution

A **heuristic scoring system** evaluates every potential break point (space character) and selects the highest-scoring position.

### Constraints

| Parameter | Segment Split | Line Break |
|-----------|---------------|------------|
| Soft cap | ~27 chars | 18 chars |
| Hard cap | 43 chars | 25 chars |
| Max lines | - | 2 |

---

## Scoring System

Each space position receives a score based on:

### 1. Linguistic Bonus (Language-Aware)

**Universal:**

| Pattern | Score | Example |
|---------|-------|---------|
| Sentence delimiter (`.?!`) | +60 | "끝났다. 이제..." |
| Clause delimiter (`,;:`) | +40 | "그러나, 놀부는..." |

**Korean (Kiwi Morpheme Analysis):**

| POS Tag | Score | Meaning |
|---------|-------|---------|
| EC (연결어미) | +30 | "~하고", "~해서" |
| EF (종결어미) | +30 | "~했다" (without punctuation) |
| JK* / JX (조사) | +20 | "놀부는", "마음에" |
| Time nouns (뒤, 후, 다음) | +30 | "먹은 뒤" |

**English:**

| Pattern | Score | Example |
|---------|-------|---------|
| Conjunction (and, but, because) | +20 | "...happy, and then..." |
| Preposition (to, for, with) | +20 | "...went to the..." |

### 2. Distance Penalty

Deviation from the target position is penalized quadratically:

```
penalty = -(distance_ratio²) × weight
```

This creates a preference for balanced splits while still allowing linguistic factors to override.

### 3. Orphan Penalty

Extreme splits leaving < 3 characters on either side receive `-100` penalty.

### 4. Tight Binding Penalty

Certain patterns should never be split:

| Pattern | Penalty | Reason |
|---------|---------|--------|
| 관형형 + 명사 (ETM + N) | -50 | "예쁜 | 꽃" breaks modifier |
| 본용언 + 보조용언 (V + VX) | -50 | "먹어 | 버렸다" breaks compound verb |
| 관형사 + 체언 (MM + N) | -25 | "이 | 사람" breaks determiner |

---

## Timestamp Calculation

When a user edits subtitle text, the original word timestamps may no longer align. The system uses **fuzzy matching** to recover:

1. Extract the word immediately before the split point
2. Search nearby words (±15 from estimated position) using RapidFuzz
3. Return matched word's end timestamp

This tolerates typo corrections, word reordering, and minor edits.

---

## Implementation Notes

- **Kiwi lazy-loading:** Morpheme analyzer initialized only when Korean text detected
- **Sentence-level caching:** Morpheme analysis runs once per sentence, cached by space index
- **Fallback:** If no valid break found, use `strict=True` mode (distance-only)
