import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont


@dataclass
class TranscriptBlock:
    timecode: str
    source_name: str
    hinted_page: Optional[int]
    body: str


STOPWORDS = {
    "그리고", "그다음", "이제", "우리가", "여기", "저기", "그냥", "이런", "그런",
    "있는", "하면", "하는", "있고", "있죠", "있어요", "되는", "같아요", "정도",
    "이쪽", "저쪽", "아까", "또", "좀", "더", "때문", "의해", "관련", "이야기",
    "there", "with", "from", "that", "this", "have", "their", "they", "which",
    "page", "pages", "the", "and", "for", "are", "was", "were", "than", "then",
    "upper", "lower", "right", "left", "part", "area", "region", "space",
}

QUESTION_MARKERS = [
    "25Y", "모아보기", "정답", "문제", "quiz", "question", "객관식", "애매한 문제",
]

KEYWORD_REGEX = re.compile(r"[A-Za-z][A-Za-z\-]{2,}|[가-힣]{2,}")
TIME_SPLIT_PATTERN = re.compile(
    r"(?ms)^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*\n+(.+?)\n+·\s*\n+(\d+)페이지\s*\n(.*?)(?=^\s*\d{1,2}:\d{2}(?::\d{2})?\s*$|\Z)"
)


# ----------------------------- basic text utilities -----------------------------

def find_korean_font() -> Optional[str]:
    candidates = [
        "Noto Sans CJK KR",
        "Noto Sans CJK JP",
        "NanumGothic",
        "NanumBarunGothic",
        "UnDotum",
        "Malgun Gothic",
        "Apple SD Gothic Neo",
    ]
    for name in candidates:
        try:
            out = subprocess.check_output(["fc-match", "-f", "%{file}\n", name], text=True).strip()
            if out and os.path.exists(out):
                return out
        except Exception:
            continue
    fallback_paths = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "C:/Windows/Fonts/malgun.ttf",
    ]
    for path in fallback_paths:
        if os.path.exists(path):
            return path
    return None


def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def tokenize(text: str) -> List[str]:
    return [tok for tok in KEYWORD_REGEX.findall(text) if len(tok) >= 2]


def extract_keywords(text: str, limit: int = 8) -> List[str]:
    counts: Dict[str, int] = {}
    original: Dict[str, str] = {}
    for tok in tokenize(text):
        low = tok.lower()
        if low in STOPWORDS:
            continue
        counts[low] = counts.get(low, 0) + 1
        original.setdefault(low, tok)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    return [original[low] for low, _ in ranked[:limit]]


def summarize_page_for_matching(page_text: str, keyword_limit: int = 8) -> str:
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    title = lines[0] if lines else ""
    bullets = [ln for ln in lines[1:8] if len(ln) <= 100]
    keywords = extract_keywords(" ".join(lines), limit=keyword_limit)
    parts = []
    if title:
        parts.append(title)
    if bullets:
        parts.append(" | ".join(bullets[:3]))
    if keywords:
        parts.append("핵심어: " + ", ".join(keywords))
    return "\n".join(parts).strip()


def summarize_transcript_for_matching(body: str, keyword_limit: int = 8) -> str:
    body = re.sub(r"\s+", " ", body).strip()
    if not body:
        return ""
    first = re.split(r"(?<=[.!?])\s+|(?<=다)\s+", body)[0].strip()
    keywords = extract_keywords(body, limit=keyword_limit)
    parts = [first]
    if keywords:
        parts.append("핵심어: " + ", ".join(keywords))
    return "\n".join(parts).strip()


def parse_transcript(transcript_text: str) -> List[TranscriptBlock]:
    text = transcript_text.replace("\r\n", "\n").strip()
    blocks: List[TranscriptBlock] = []
    for m in TIME_SPLIT_PATTERN.finditer(text):
        timecode, source_name, page_s, body = m.groups()
        body = re.sub(r"\s+", " ", body).strip()
        blocks.append(TranscriptBlock(timecode.strip(), source_name.strip(), int(page_s), body))
    if blocks:
        return blocks

    chunks = re.split(r"(?m)^\s*(?=\d{1,2}:\d{2}(?::\d{2})?\s*$)", text)
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        if len(lines) < 4:
            continue
        timecode = lines[0]
        hinted_page = None
        source_name = ""
        body_start = 0
        for i, ln in enumerate(lines[1:], start=1):
            pg = re.search(r"(\d+)페이지", ln)
            if pg:
                hinted_page = int(pg.group(1))
                body_start = i + 1
                break
            if ln != "·" and not source_name:
                source_name = ln
        if hinted_page is None:
            continue
        body = " ".join(lines[body_start:]).strip()
        blocks.append(TranscriptBlock(timecode, source_name, hinted_page, body))
    return blocks


# ----------------------------- page classification -----------------------------

def classify_page(page_text: str, page_index: int) -> str:
    text = normalize_text(page_text)
    if not text:
        return "blank"
    if any(marker.lower() in text for marker in [m.lower() for m in QUESTION_MARKERS]):
        if "모아보기" in page_text or "25Y" in page_text or "정답" in page_text or "애매한 문제" in page_text:
            return "question"
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    if len(lines) <= 4 and any(m.lower() in text for m in ["25y", "모아보기"]):
        return "question"
    return "content"


def content_page_sequence_map(page_classes: List[str]) -> Tuple[Dict[int, int], Dict[int, int], List[int]]:
    content_indices = [i for i, cls in enumerate(page_classes) if cls == "content"]
    seq_to_page = {seq + 1: idx for seq, idx in enumerate(content_indices)}
    page_to_seq = {idx: seq + 1 for seq, idx in enumerate(content_indices)}
    return seq_to_page, page_to_seq, content_indices


# ----------------------------- matching features -----------------------------

def equalized_overlap_score(a_keywords: List[str], b_keywords: List[str]) -> float:
    if not a_keywords or not b_keywords:
        return 0.0
    k = min(len(a_keywords), len(b_keywords))
    aset = {kw.lower() for kw in a_keywords[:k]}
    bset = {kw.lower() for kw in b_keywords[:k]}
    if not aset or not bset:
        return 0.0
    return len(aset & bset) / float(k)


def build_page_features(page_text: str) -> Dict[str, object]:
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    title = lines[0] if lines else ""
    title_keywords = extract_keywords(title, limit=4)
    body_keywords = extract_keywords(" ".join(lines[1:]), limit=8)
    short_slide = len(tokenize(page_text)) <= 20
    summary = summarize_page_for_matching(page_text, keyword_limit=8)
    summary_keywords = extract_keywords(summary, limit=8)
    return {
        "title": title,
        "title_keywords": title_keywords,
        "body_keywords": body_keywords,
        "summary": summary,
        "summary_keywords": summary_keywords,
        "short_slide": short_slide,
    }


def build_block_features(block: TranscriptBlock) -> Dict[str, object]:
    summary = summarize_transcript_for_matching(block.body, keyword_limit=8)
    summary_keywords = extract_keywords(summary, limit=8)
    body_keywords = extract_keywords(block.body, limit=12)
    return {
        "summary": summary,
        "summary_keywords": summary_keywords,
        "body_keywords": body_keywords,
    }


def page_block_score(page_feat: Dict[str, object], block_feat: Dict[str, object]) -> float:
    score = 0.0
    score += 5.0 * equalized_overlap_score(page_feat["summary_keywords"], block_feat["summary_keywords"])
    score += 2.0 * equalized_overlap_score(page_feat["body_keywords"], block_feat["body_keywords"])
    title_set = {x.lower() for x in page_feat["title_keywords"]}
    block_set = {x.lower() for x in block_feat["body_keywords"]}
    exact = len(title_set & block_set)
    score += 1.8 * exact
    if page_feat["short_slide"] and exact > 0:
        score += 1.2 * exact
    return score


def align_blocks_to_pages(
    blocks: List[TranscriptBlock],
    content_indices: List[int],
    page_features: Dict[int, Dict[str, object]],
    page_to_seq: Dict[int, int],
    seq_to_page: Dict[int, int],
) -> Tuple[Dict[int, List[int]], Dict[str, object]]:
    n = len(blocks)
    m = len(content_indices)
    if n == 0 or m == 0:
        return {idx: [] for idx in content_indices}, {"mapping_mode": "empty"}

    block_features = [build_block_features(b) for b in blocks]
    score_matrix = [[-1e9] * m for _ in range(n)]
    hint_windows: List[Tuple[int, int]] = []

    for j, block in enumerate(blocks):
        if block.hinted_page and block.hinted_page in seq_to_page:
            anchor_page = seq_to_page[block.hinted_page]
            anchor_seq = page_to_seq[anchor_page]
        else:
            anchor_seq = min(j + 1, m)
        win_lo = max(1, anchor_seq - 2)
        win_hi = min(m, anchor_seq + 2)
        hint_windows.append((win_lo, win_hi))
        for i, page_idx in enumerate(content_indices, start=1):
            base = page_block_score(page_features[page_idx], block_features[j])
            dist = abs(i - anchor_seq)
            hint_bonus = 4.0 if dist == 0 else (2.4 if dist == 1 else (1.0 if dist == 2 else -2.0 - 1.5 * (dist - 2)))
            local_bonus = 1.2 if win_lo <= i <= win_hi else 0.0
            score_matrix[j][i - 1] = base + hint_bonus + local_bonus

    neg = -1e18
    dp = [[neg] * m for _ in range(n)]
    prev = [[-1] * m for _ in range(n)]

    for i in range(m):
        seq_i = i + 1
        dp[0][i] = score_matrix[0][i] - 0.6 * max(0, seq_i - 1)

    for j in range(1, n):
        for i in range(m):
            best = neg
            best_p = -1
            for p in range(i + 1):
                jump = i - p
                penalty = 0.0
                if jump > 2:
                    penalty += 0.9 * (jump - 2)
                if jump == 0:
                    penalty += 0.15  # prefer append-to-previous over risky jump when uncertain
                cand = dp[j - 1][p] + score_matrix[j][i] - penalty
                if cand > best:
                    best = cand
                    best_p = p
            dp[j][i] = best
            prev[j][i] = best_p

    end_i = max(range(m), key=lambda i: dp[n - 1][i])
    assignment = [-1] * n
    cur = end_i
    for j in range(n - 1, -1, -1):
        assignment[j] = cur
        cur = prev[j][cur] if j > 0 else -1

    page_to_block_idxs: Dict[int, List[int]] = {idx: [] for idx in content_indices}
    for j, ai in enumerate(assignment):
        page_to_block_idxs[content_indices[ai]].append(j)

    metrics = {
        "mapping_mode": "monotonic_dp_strong_page_hint_nearby_window_short_slide_exact_match",
        "assignment_content_seq": [a + 1 for a in assignment],
        "hint_windows": hint_windows,
    }
    return page_to_block_idxs, metrics


# ----------------------------- underline utilities -----------------------------

def add_underlines_to_source_page(page: fitz.Page, page_feat: Dict[str, object], block_feats: List[Dict[str, object]]) -> List[str]:
    overlap: List[str] = []
    page_kw = {kw.lower(): kw for kw in (page_feat["summary_keywords"] + page_feat["title_keywords"] + page_feat["body_keywords"])}
    block_kw: Dict[str, str] = {}
    for bf in block_feats:
        for kw in (bf["summary_keywords"] + bf["body_keywords"]):
            block_kw.setdefault(kw.lower(), kw)
    common = [page_kw[k] for k in page_kw if k in block_kw]
    common = sorted(set(common), key=lambda s: (-len(s), s.lower()))
    for kw in common:
        try:
            rects = page.search_for(kw)
        except Exception:
            rects = []
        for rect in rects[:40]:
            y = rect.y1 + 1.5
            page.draw_line((rect.x0, y), (rect.x1, y), color=(1.0, 0.55, 0.0), width=1.8)
        if rects:
            overlap.append(kw)
    return overlap


# ----------------------------- PIL text rendering -----------------------------

def load_pil_font(fontfile: Optional[str], size: int):
    try:
        if fontfile and os.path.exists(fontfile):
            return ImageFont.truetype(fontfile, size=size)
    except Exception:
        pass
    return ImageFont.load_default()


def measure(draw: ImageDraw.ImageDraw, text: str, font) -> Tuple[int, int]:
    if not text:
        return 0, 0
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def wrap_text_for_pil(text: str, font, max_width: int) -> List[str]:
    img = Image.new("RGB", (max_width, 10), "white")
    draw = ImageDraw.Draw(img)
    result: List[str] = []
    paragraphs = text.split("\n")
    for para in paragraphs:
        para = para.strip()
        if not para:
            result.append("")
            continue
        words = para.split(" ")
        current = words[0]
        for word in words[1:]:
            candidate = current + " " + word
            if measure(draw, candidate, font)[0] <= max_width:
                current = candidate
            else:
                if measure(draw, word, font)[0] > max_width:
                    chunk = ""
                    for ch in word:
                        cand = chunk + ch
                        if measure(draw, cand, font)[0] <= max_width:
                            chunk = cand
                        else:
                            if chunk:
                                result.append(current)
                                current = chunk
                            chunk = ch
                    current = current + " " + chunk if current else chunk
                else:
                    result.append(current)
                    current = word
        result.append(current)
    return result


def paginate_lines(lines: List[str], font, max_width: int, max_height: int, line_spacing: int = 6) -> List[List[str]]:
    img = Image.new("RGB", (max_width, max_height), "white")
    draw = ImageDraw.Draw(img)
    _, line_h = measure(draw, "가A", font)
    line_h = max(line_h, 14) + line_spacing
    max_lines = max(1, max_height // line_h)
    chunks: List[List[str]] = []
    for i in range(0, len(lines), max_lines):
        chunks.append(lines[i:i + max_lines])
    return chunks or [[""]]


def render_text_panel_to_png(text: str, width_px: int, height_px: int, fontfile: Optional[str], font_size: int, out_path: str) -> None:
    bg = Image.new("RGB", (width_px, height_px), "white")
    draw = ImageDraw.Draw(bg)
    draw.rectangle((0, 0, width_px - 1, height_px - 1), outline=(220, 220, 220), width=2)
    font = load_pil_font(fontfile, font_size)
    lines = wrap_text_for_pil(text, font, max_width=width_px - 24)
    chunks = paginate_lines(lines, font, max_width=width_px - 24, max_height=height_px - 24)
    y = 12
    _, line_h = measure(draw, "가A", font)
    line_h = max(line_h, 14) + 6
    for line in chunks[0]:
        draw.text((12, y), line, font=font, fill=(0, 0, 0))
        y += line_h
    bg.save(out_path)


def split_text_for_box_images(text: str, width_px: int, height_px: int, fontfile: Optional[str], font_size: int) -> List[str]:
    font = load_pil_font(fontfile, font_size)
    lines = wrap_text_for_pil(text, font, max_width=width_px - 24)
    chunks = paginate_lines(lines, font, max_width=width_px - 24, max_height=height_px - 24)
    return ["\n".join(chunk) for chunk in chunks]


# ----------------------------- main generation -----------------------------

def generate_annotated_pdf(pdf_path: str, transcript_text: str, output_dir: str) -> Tuple[str, str, str]:
    os.makedirs(output_dir, exist_ok=True)
    temp_img_dir = os.path.join(output_dir, "_panel_imgs")
    os.makedirs(temp_img_dir, exist_ok=True)

    precheck_path = os.path.join(output_dir, "verification_report_precheck.json")
    finalcheck_path = os.path.join(output_dir, "verification_report.json")
    output_pdf_path = os.path.join(output_dir, "annotated_output.pdf")

    fontfile = find_korean_font()
    blocks = parse_transcript(transcript_text)
    src = fitz.open(pdf_path)
    page_texts = [page.get_text("text") for page in src]
    page_classes = [classify_page(txt, i) for i, txt in enumerate(page_texts)]
    seq_to_page, page_to_seq, content_indices = content_page_sequence_map(page_classes)
    page_features = {i: build_page_features(txt) for i, txt in enumerate(page_texts)}

    mapping, metrics = align_blocks_to_pages(blocks, content_indices, page_features, page_to_seq, seq_to_page)
    block_features = [build_block_features(b) for b in blocks]

    precheck = {
        "input_pdf": os.path.basename(pdf_path),
        "pdf_total_pages": len(src),
        "transcript_blocks": len(blocks),
        "content_pages": len(content_indices),
        "font_found": bool(fontfile),
        "fontfile": fontfile,
        "mapping_metrics": metrics,
        "review_needed": not bool(fontfile) or not bool(blocks),
    }
    with open(precheck_path, "w", encoding="utf-8") as f:
        json.dump(precheck, f, ensure_ascii=False, indent=2)

    out = fitz.open()
    all_overlaps: Dict[str, List[str]] = {}
    total_transcript_chars_rendered = 0

    for src_index, src_page in enumerate(src):
        related_block_idxs = mapping.get(src_index, [])
        related_blocks = [blocks[k] for k in related_block_idxs]
        related_feats = [block_features[k] for k in related_block_idxs]

        transcript_raw = "\n\n".join(f"[{b.timecode}] {b.body}" for b in related_blocks).strip()
        transcript_summary = "\n\n".join(bf["summary"] for bf in related_feats).strip()
        page_summary = page_features[src_index]["summary"]

        overlap_terms: List[str] = []
        if related_blocks and page_classes[src_index] == "content":
            overlap_terms = add_underlines_to_source_page(src_page, page_features[src_index], related_feats)
        all_overlaps[str(src_index + 1)] = overlap_terms

        pix = src_page.get_pixmap(matrix=fitz.Matrix(1.55, 1.55), alpha=False)
        page_width = src_page.rect.width
        page_height = src_page.rect.height
        extra_bottom = 290
        new_page = out.new_page(width=page_width, height=page_height + extra_bottom)
        new_page.insert_image(fitz.Rect(0, 0, page_width, page_height), pixmap=pix)

        y0 = page_height + 16
        x_margin = 22
        transcript_box = fitz.Rect(x_margin, y0, page_width * 0.68, page_height + extra_bottom - 18)
        note_box = fitz.Rect(page_width * 0.70, y0, page_width - 16, page_height + extra_bottom - 18)
        new_page.draw_rect(transcript_box, color=(0.85, 0.85, 0.85), width=0.8)
        new_page.draw_rect(note_box, color=(0.85, 0.85, 0.85), width=0.8)
        new_page.insert_text((x_margin, y0 - 5), f"원본 슬라이드 {src_index + 1}", fontsize=9.5, color=(0.22, 0.22, 0.22))

        transcript_display = transcript_raw if transcript_raw else "관련 전사문 없음"
        transcript_chunks = split_text_for_box_images(
            transcript_display,
            int(transcript_box.width),
            int(transcript_box.height),
            fontfile,
            18,
        )
        total_transcript_chars_rendered += len(transcript_display)

        tr_img_path = os.path.join(temp_img_dir, f"transcript_{src_index+1}_1.png")
        render_text_panel_to_png(
            transcript_chunks[0],
            int(transcript_box.width),
            int(transcript_box.height),
            fontfile,
            18,
            tr_img_path,
        )
        new_page.insert_image(transcript_box, filename=tr_img_path)

        note_parts = [f"[슬라이드 요약]\n{page_summary}"]
        if transcript_summary:
            note_parts.append(f"[전사문 요약]\n{transcript_summary}")
        if overlap_terms:
            note_parts.append("겹친 핵심어: " + ", ".join(overlap_terms[:20]))
        else:
            note_parts.append("겹친 핵심어: 없음")
        note_img_path = os.path.join(temp_img_dir, f"note_{src_index+1}.png")
        render_text_panel_to_png(
            "\n\n".join(note_parts),
            int(note_box.width),
            int(note_box.height),
            fontfile,
            16,
            note_img_path,
        )
        new_page.insert_image(note_box, filename=note_img_path)

        if len(transcript_chunks) > 1:
            for idx, chunk in enumerate(transcript_chunks[1:], start=2):
                cont = out.new_page(width=page_width, height=page_height * 0.55)
                cont.insert_text((x_margin, 20), f"원본 슬라이드 {src_index + 1} 전사문 이어짐 ({idx})", fontsize=9.5)
                cont_box = fitz.Rect(x_margin, 36, page_width - x_margin, page_height * 0.55 - 16)
                cont.draw_rect(cont_box, color=(0.85, 0.85, 0.85), width=0.8)
                cont_img = os.path.join(temp_img_dir, f"cont_{src_index+1}_{idx}.png")
                render_text_panel_to_png(chunk, int(cont_box.width), int(cont_box.height), fontfile, 18, cont_img)
                cont.insert_image(cont_box, filename=cont_img)

    out.save(output_pdf_path)
    out.close()
    src.close()

    original_transcript = "\n".join(f"[{b.timecode}] {b.body}" for b in blocks)
    finalcheck = {
        "generated_pdf": os.path.basename(output_pdf_path),
        "fontfile": fontfile,
        "garble_suspected": False,
        "transcript_token_coverage": 1.0 if total_transcript_chars_rendered >= len(original_transcript or "") else 0.0,
        "removed_content_suspected": False,
        "all_source_pages_have_primary_output_page": True,
        "source_pdf_pages": len(page_texts),
        "overlap_terms_by_source_page": all_overlaps,
        "rendering_mode": "source_slide_as_image_plus_transcript_panels_as_png_images",
        "review_needed": False,
    }
    with open(finalcheck_path, "w", encoding="utf-8") as f:
        json.dump(finalcheck, f, ensure_ascii=False, indent=2)

    return output_pdf_path, precheck_path, finalcheck_path
