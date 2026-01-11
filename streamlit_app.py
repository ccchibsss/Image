# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
photo_processor_combined_auto_ai.py
Интеграция улучшенных методов для более чёткой детекции фона:
- feather_mask
- refine_with_grabcut
- improve_background_mask (улучшенная версия)
- detect_background_and_objects_improved (интеграция)
"""

from __future__ import annotations
import argparse
import io
import sys
import tempfile
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

# Опциональные зависимости
try:
    import onnxruntime as ort
except ImportError:
    ort = None

try:
    from rembg import remove as rembg_remove
    HAS_REMBG = True
except ImportError:
    rembg_remove = None
    HAS_REMBG = False

try:
    import streamlit as st
except ImportError:
    st = None

try:
    from transformers import SamForSegmentation, SamProcessor, SamAutomaticMaskGenerator
    HAS_SAM = True
except ImportError:
    SamForSegmentation = None
    SamProcessor = None
    SamAutomaticMaskGenerator = None
    HAS_SAM = False

HAS_STREAMLIT = st is not None

# Логирование
def setup_logger():
    fn = f"pp_auto_ai_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        handlers=[
                            logging.FileHandler(fn, encoding="utf-8"),
                            logging.StreamHandler()
                        ])
    return logging.getLogger("pp_auto_ai")

logger = setup_logger()

# Модельные пути / параметры
ONNX_WM_PATH = Path("watermark_segmentation.onnx")
SAM_PRETRAIN = "facebook/sam-vit-huge"

onnx_session = None
if ort is not None and ONNX_WM_PATH.exists():
    try:
        onnx_session = ort.InferenceSession(str(ONNX_WM_PATH))
        logger.info("Загружена ONNX модель: %s", ONNX_WM_PATH)
    except Exception:
        logger.exception("Ошибка загрузки ONNX модели")

_sam_generator = None
def load_sam_lazy():
    global _sam_generator
    if _sam_generator is not None:
        return _sam_generator
    if not HAS_SAM:
        return None
    try:
        model = SamForSegmentation.from_pretrained(SAM_PRETRAIN)
        processor = SamProcessor.from_pretrained(SAM_PRETRAIN)
        mask_gen = SamAutomaticMaskGenerator(model)
        _sam_generator = (mask_gen, processor)
        logger.info("SAM готов")
        return _sam_generator
    except:
        logger.exception("Ошибка загрузки SAM")
        _sam_generator = None
        return None

# Конфигурации
@dataclass
class WatermarkParams:
    threshold: int = 220
    adaptive: bool = True
    block_size: int = 31
    c: int = 10
    min_area: int = 50
    max_area: int = 5000
    radius: int = 5
    use_ns: bool = True

    def normalized(self) -> "WatermarkParams":
        bs = max(3, int(self.block_size))
        if bs % 2 == 0:
            bs += 1
        return WatermarkParams(
            threshold=int(self.threshold),
            adaptive=bool(self.adaptive),
            block_size=bs,
            c=int(self.c),
            min_area=max(1, int(self.min_area)),
            max_area=max(1, int(self.max_area)),
            radius=max(1, int(self.radius)),
            use_ns=bool(self.use_ns),
        )

@dataclass
class ProcessingConfig:
    remove_bg: bool = True
    remove_wm: bool = True
    auto_ai: bool = True
    wm_params: WatermarkParams = field(default_factory=WatermarkParams)
    fmt: str = "PNG"
    jpeg_q: int = 95
    target_width: Optional[int] = None
    target_height: Optional[int] = None
    inp: Path = Path("./input")
    outp: Path = Path("./output")

def ensure_dir(p: Path):
    try:
        p.mkdir(parents=True, exist_ok=True)
    except:
        logger.exception("Ошибка при создании директории %s", p)

# ----------------- Улучшенные функции маски фона -----------------
def feather_mask(bin_mask: np.ndarray, feather_px: int = 15) -> np.ndarray:
    if bin_mask.dtype != np.uint8:
        bin_mask = (bin_mask > 0).astype(np.uint8) * 255
    mask = (bin_mask > 0).astype(np.uint8)
    if mask.sum() == 0:
        return np.zeros_like(bin_mask, dtype=np.uint8)
    dist_fg = cv2.distanceTransform(mask, cv2.DIST_L2, 5).astype(np.float32)
    dist_bg = cv2.distanceTransform(1 - mask, cv2.DIST_L2, 5).astype(np.float32)
    if feather_px > 0:
        dist_fg = np.minimum(dist_fg, feather_px).astype(np.float32)
        dist_bg = np.minimum(dist_bg, feather_px).astype(np.float32)
    alpha = dist_fg / (dist_fg + dist_bg + 1e-8)
    alpha = np.clip(alpha, 0.0, 1.0)
    return (alpha * 255).astype(np.uint8)

def refine_with_grabcut(image_rgb: np.ndarray, init_mask: np.ndarray,
                        iter_count: int = 5, sure_fg_erode: int = 3,
                        sure_bg_dilate: int = 5) -> np.ndarray:
    h, w = init_mask.shape[:2]
    if image_rgb.shape[:2] != (h, w):
        raise ValueError("image and mask sizes mismatch")
    img_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    bin_mask = (init_mask > 0).astype(np.uint8) * 255
    k_er = max(3, 2 * (sure_fg_erode // 2) + 1)
    k_bg = max(3, 2 * (sure_bg_dilate // 2) + 1)
    kernel_fg = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_er, k_er))
    kernel_bg = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_bg, k_bg))
    sure_fg = cv2.erode(bin_mask, kernel_fg, iterations=1)
    sure_bg = cv2.dilate(bin_mask, kernel_bg, iterations=1)
    sure_bg = cv2.bitwise_not(sure_bg)
    gc_mask = np.full((h, w), cv2.GC_PR_BGD, dtype=np.uint8)
    gc_mask[sure_bg > 0] = cv2.GC_BGD
    gc_mask[sure_fg > 0] = cv2.GC_FGD
    if sure_fg.sum() == 0:
        cx, cy = w // 2, h // 2
        padx = max(10, w // 10)
        pady = max(10, h // 10)
        gc_mask[cy - pady:cy + pady, cx - padx:cx + padx] = cv2.GC_PR_FGD
    bgdModel = np.zeros((1, 65), np.float64)
    fgdModel = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(img_bgr, gc_mask, None, bgdModel, fgdModel, iter_count, cv2.GC_INIT_WITH_MASK)
        result_mask = np.where((gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    except Exception:
        result_mask = bin_mask.copy()
    return result_mask

def improve_background_mask(image_np: np.ndarray, feather_ratio: float = 0.02) -> np.ndarray:
    h, w = image_np.shape[:2]
    lab = cv2.cvtColor(image_np, cv2.COLOR_RGB2Lab)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_eq = clahe.apply(l)
    lab_eq = cv2.merge([l_eq, a, b])
    img_eq = cv2.cvtColor(lab_eq, cv2.COLOR_Lab2RGB)
    pixels = np.float32(np.stack([a.flatten(), b.flatten()], axis=1))
    try:
        _, labels, centers = cv2.kmeans(pixels, 2, None,
                                        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.5),
                                        10, cv2.KMEANS_PP_CENTERS)
        labels = labels.flatten().reshape((h, w))
        center_vals = centers.mean(axis=1)
        bg_label = int(np.argmin(center_vals))
        mask_bg = (labels == bg_label).astype(np.uint8) * 255
    except Exception:
        mask_bg = np.ones((h, w), dtype=np.uint8) * 255
    gray = cv2.cvtColor(img_eq, cv2.COLOR_RGB2GRAY)
    sobx = cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3)
    soby = cv2.Sobel(gray, cv2.CV_16S, 0, 1, ksize=3)
    grad = cv2.magnitude(sobx.astype(np.float32), soby.astype(np.float32)).astype(np.uint8)
    _, grad_th = cv2.threshold(grad, max(20, int(np.median(grad) * 1.5)), 255, cv2.THRESH_BINARY)
    grad_th = cv2.dilate(grad_th, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=1)
    combined = cv2.bitwise_and(mask_bg, cv2.bitwise_not(grad_th))
    ksize = max(3, min(h, w) // 100)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksize, ksize))
    combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, kernel, iterations=1)
    combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(combined.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area_thresh = (h * w) * 0.0005
    mask_filtered = np.zeros_like(combined)
    for c in contours:
        if cv2.contourArea(c) >= area_thresh:
            cv2.drawContours(mask_filtered, [c], -1, 255, thickness=-1)
    sure_fg_erode = max(3, min(h, w) // 150)
    sure_bg_dilate = max(5, min(h, w) // 60)
    try:
        init_fg_like = cv2.bitwise_not(mask_filtered)
        grabcut_result = refine_with_grabcut(image_np, init_fg_like,
                                             iter_count=5,
                                             sure_fg_erode=sure_fg_erode,
                                             sure_bg_dilate=sure_bg_dilate)
        bg_mask_final = cv2.bitwise_not(grabcut_result)
    except Exception:
        bg_mask_final = mask_filtered
    feather_px = max(3, int(min(h, w) * feather_ratio))
    soft_bg = feather_mask(bg_mask_final, feather_px)
    return soft_bg

def detect_background_and_objects_improved(image_np: np.ndarray,
                                           color_threshold: int = 30,
                                           area_limits: Tuple[float, float] = (0.0005, 0.2)) -> np.ndarray:
    soft_bg = improve_background_mask(image_np)
    bin_bg = (soft_bg > 127).astype(np.uint8) * 255
    inv = cv2.bitwise_not(bin_bg)
    contours, _ = cv2.findContours(inv, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = image_np.shape[:2]
    img_area = h * w
    min_area, max_area = area_limits[0] * img_area, area_limits[1] * img_area
    obj_mask = np.zeros((h, w), dtype=np.uint8)
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area or area > max_area:
            continue
        cv2.drawContours(obj_mask, [c], -1, 255, thickness=-1)
    if obj_mask.sum() > 0:
        bg_mask = cv2.bitwise_and(soft_bg, cv2.bitwise_not(obj_mask))
    else:
        bg_mask = soft_bg
    return bg_mask

# ----------------- Остальной функционал (с сохранением логики)
# -----------------
def remove_background_rembg(pil_img: Image.Image, cfg: ProcessingConfig) -> Optional[np.ndarray]:
    if not cfg.remove_bg or not HAS_REMBG or rembg_remove is None:
        return None
    try:
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        buf.seek(0)
        out = rembg_remove(buf.read())
        img = Image.open(io.BytesIO(out)).convert("RGBA")
        arr = np.array(img)
        alpha = arr[..., 3]
        bg_mask = (alpha == 0).astype(np.uint8) * 255
        logger.info("rembg сгенерировал маску фона")
        return bg_mask
    except:
        logger.exception("Ошибка rembg")
        return None

def segment_with_sam(pil_img: Image.Image) -> np.ndarray:
    gen_proc = load_sam_lazy()
    if gen_proc is None:
        return np.zeros((pil_img.height, pil_img.width), dtype=np.uint8)
    try:
        mask_gen, _ = gen_proc
        masks = mask_gen.generate(np.array(pil_img.convert("RGB")))
        combined = np.zeros((pil_img.height, pil_img.width), dtype=np.uint8)
        for m in masks:
            combined = np.maximum(combined, (m.get("segmentation") * 255).astype(np.uint8))
        return combined
    except:
        logger.exception("Ошибка сегментации SAM")
        return np.zeros((pil_img.height, pil_img.width), dtype=np.uint8)

def segment_with_onnx(pil_img: Image.Image) -> np.ndarray:
    if onnx_session is None:
        return np.zeros((pil_img.height, pil_img.width), dtype=np.uint8)
    try:
        inp = onnx_session.get_inputs()[0]
        shape = inp.shape
        _, c, h, w = shape if len(shape) == 4 else (1, 3, 256, 256)
        resized = pil_img.resize((w, h)).convert("RGB")
        arr = np.array(resized).astype(np.float32) / 255.0
        tensor = np.transpose(arr, (2, 0, 1))
        tensor = np.expand_dims(tensor, axis=0).astype(np.float32)
        res = onnx_session.run(None, {inp.name: tensor})
        pred = res[0]
        pred_map = pred[0, 0] if pred.ndim == 4 else pred[0]
        mask_resized = cv2.resize(pred_map.astype(np.float32), (pil_img.width, pil_img.height))
        return (mask_resized > 0.5).astype(np.uint8) * 255
    except:
        logger.exception("Ошибка сегментации ONNX")
        return np.zeros((pil_img.height, pil_img.width), dtype=np.uint8)

def detect_background_and_objects(image_np: np.ndarray,
                                  color_threshold=30,
                                  area_limits=(0.0005, 0.2)) -> np.ndarray:
    # Для обратной совместимости просто вызывает улучшенную версию
    return detect_background_and_objects_improved(image_np, color_threshold, area_limits)

def combine_masks(masks: List[np.ndarray]) -> Optional[np.ndarray]:
    if not masks:
        return None
    base = np.zeros_like(masks[0], dtype=np.uint8)
    for m in masks:
        if m is not None:
            base = np.maximum(base, m)
    return base

def auto_detect_background_and_watermark(
        image_np: np.ndarray,
        cfg: ProcessingConfig,
        use_sam=True,
        use_onnx=True
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    h, w = image_np.shape[:2]
    img_area = h * w
    bg_mask_candidates: List[np.ndarray] = []
    try:
        rembg_mask = remove_background_rembg(Image.fromarray(cv2.cvtColor(image_np, cv2.COLOR_RGB2RGBA)), cfg)
        if rembg_mask is not None:
            bg_mask_candidates.append(rembg_mask)
    except:
        pass
    fg_candidates = []
    pil = Image.fromarray(image_np)
    if use_sam and HAS_SAM:
        try:
            sam_fg = segment_with_sam(pil)
            fg_candidates.append(sam_fg)
        except:
            pass
    if use_onnx and onnx_session:
        try:
            onnx_fg = segment_with_onnx(pil)
            fg_candidates.append(onnx_fg)
        except:
            pass
    try:
        obj_mask = detect_background_and_objects_improved(image_np)
        if obj_mask is not None:
            fg_candidates.append(cv2.bitwise_not((obj_mask > 127).astype(np.uint8) * 255))
    except:
        pass
    if fg_candidates:
        fg_comb = combine_masks(fg_candidates)
        bg_from_fg = cv2.bitwise_not((fg_comb > 0).astype(np.uint8) * 255)
        bg_mask_candidates.append(bg_from_fg)
    try:
        margin = max(10, min(h, w) // 20)
        edges = []
        edges.append(image_np[:margin, :, :].reshape(-1, 3))
        edges.append(image_np[-margin:, :, :].reshape(-1, 3))
        edges.append(image_np[:, :margin, :].reshape(-1, 3))
        edges.append(image_np[:, -margin:, :].reshape(-1, 3))
        edges = np.vstack(edges).astype(np.float32)
        if edges.shape[0] > 0:
            _, labels, centers = cv2.kmeans(
                edges,
                1,
                None,
                (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0),
                5,
                cv2.KMEANS_PP_CENTERS
            )
            center = centers[0].astype(np.uint8)
            diff = np.linalg.norm(image_np.astype(np.float32) - center.reshape(1,1,3), axis=2)
            bg_color_mask = (diff < 30).astype(np.uint8) * 255
            bg_color_mask = cv2.morphologyEx(bg_color_mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7,7)))
            bg_mask_candidates.append(bg_color_mask)
    except:
        pass
    # Добавим также улучшенную маску фона как кандидат
    try:
        improved_bg = improve_background_mask(image_np)
        if improved_bg is not None:
            bg_mask_candidates.append(improved_bg)
    except:
        pass
    if bg_mask_candidates:
        stacked = np.stack(bg_mask_candidates, axis=0)
        votes = np.sum(stacked > 0, axis=0)
        # требуем хотя бы 1 голос (консервативно) — можно менять порог
        bg_mask = (votes >= 1).astype(np.uint8) * 255
    else:
        bg_mask = None
    wm_candidates: List[np.ndarray] = []
    try:
        gray = cv2.cvtColor(image_np, cv2.COLOR_RGB2GRAY)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15,15))
        tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
        _, th_tophat = cv2.threshold(tophat, max(10, int(tophat.mean()+tophat.std())), 255, cv2.THRESH_BINARY)
        wm_candidates.append(th_tophat)
        bs = 31 if min(h, w) > 200 else 15
        th_inv = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY_INV, bs, 9)
        th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, bs, 9)
        wm_candidates.extend([th_inv, th])
        blur = cv2.GaussianBlur(gray, (25,25), 0)
        diff = cv2.absdiff(gray, blur)
        _, th_diff = cv2.threshold(diff, max(8, int(diff.mean()+diff.std())), 255, cv2.THRESH_BINARY)
        wm_candidates.append(th_diff)
        combined_wm = combine_masks(wm_candidates)
        if combined_wm is None:
            combined_wm = np.zeros((h,w), dtype=np.uint8)
        kernel2 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3,3))
        combined_wm = cv2.morphologyEx(combined_wm, cv2.MORPH_OPEN, kernel2, iterations=1)
        combined_wm = cv2.morphologyEx(combined_wm, cv2.MORPH_CLOSE, kernel2, iterations=1)
        contours, _ = cv2.findContours((combined_wm>0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        wm_mask = np.zeros((h,w), dtype=np.uint8)
        for c in contours:
            area = cv2.contourArea(c)
            if area < max(20, 0.00002*img_area):
                continue
            if area > 0.1*img_area:
                continue
            x,y,ww,hh = cv2.boundingRect(c)
            ar = ww/float(hh+1e-9)
            if area < 5000 or ar > 2.0 or ar < 0.4 or (area < 0.02*img_area and (ar>1.5 or ar<0.66)):
                cv2.drawContours(wm_mask, [c], -1, 255, thickness=-1)
        if fg_candidates:
            fg_mask = combine_masks(fg_candidates)
            if fg_mask is not None:
                overlap = (cv2.bitwise_and(wm_mask, fg_mask) > 0).astype(np.uint8)
                if overlap.sum() > 0:
                    wm_mask = cv2.bitwise_and(wm_mask, cv2.bitwise_not(fg_mask))
        wm_mask = cv2.morphologyEx(wm_mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(3,3)), iterations=1)
        wm_mask = cv2.morphologyEx(wm_mask, cv2.MORPH_DILATE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(3,3)), iterations=1)
        if wm_mask.sum() > 0:
            logger.info("heuristic watermark mask found, pixels=%d", int(wm_mask.sum()/255))
            wm_candidates.append(wm_mask)
    except:
        pass
    try:
        if onnx_session is not None:
            onnx_pred = segment_with_onnx(pil)
            if onnx_pred is not None and onnx_pred.sum() > 0:
                wm_candidates.append(onnx_pred)
                logger.info("ONNX watermark mask added")
    except:
        pass
    final_wm = combine_masks(wm_candidates) if wm_candidates else None
    if final_wm is not None:
        wm_area = np.count_nonzero(final_wm) / 255
        if wm_area > 0.25 * img_area:
            logger.info("отбрасываем wm (слишком большая область) %.3f", wm_area/img_area)
            final_wm = None
    return bg_mask, final_wm

def remove_watermark(img_cv: np.ndarray, mask: np.ndarray, params: WatermarkParams) -> np.ndarray:
    if mask is None or mask.sum() == 0:
        return img_cv
    try:
        params = params.normalized()
        has_alpha = img_cv.ndim == 3 and img_cv.shape[2] == 4
        bgr = img_cv[..., :3].copy()
        inpaint_telea = cv2.inpaint(bgr, mask, int(params.radius), cv2.INPAINT_TELEA)
        chosen = inpaint_telea
        if params.use_ns:
            try:
                inpaint_ns = cv2.inpaint(bgr, mask, int(params.radius), cv2.INPAINT_NS)
                m = mask.astype(bool)
                if m.any():
                    telea_err = float(np.mean(np.abs(inpaint_telea[m] - bgr[m])))
                    ns_err = float(np.mean(np.abs(inpaint_ns[m] - bgr[m])))
                    chosen = inpaint_ns if ns_err <= telea_err else inpaint_telea
            except:
                pass
        if has_alpha:
            out = cv2.cvtColor(chosen, cv2.COLOR_BGR2BGRA)
            out[..., 3] = img_cv[..., 3]
        else:
            out = chosen
        return out
    except:
        logger.exception("Ошибка при удалении водяного знака")
        return img_cv

def resize_cv(img_cv: np.ndarray, w_target: Optional[int], h_target: Optional[int]) -> np.ndarray:
    h, w = img_cv.shape[:2]
    if not w_target and not h_target:
        return img_cv
    if w_target and h_target:
        return cv2.resize(img_cv, (w_target, h_target), interpolation=cv2.INTER_AREA)
    if w_target:
        scale = w_target / w
        return cv2.resize(img_cv, (w_target, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    if h_target:
        scale = h_target / h
        return cv2.resize(img_cv, (max(1, int(w * scale)), h_target), interpolation=cv2.INTER_AREA)
    return img_cv

def save_cv_image(img_cv: np.ndarray, out_path: Path, cfg: ProcessingConfig) -> bool:
    try:
        ensure_dir(out_path.parent)
        img_cv = resize_cv(img_cv, cfg.target_width, cfg.target_height)
        if img_cv.ndim == 2:
            pil = Image.fromarray(cv2.cvtColor(img_cv, cv2.COLOR_GRAY2RGB))
        elif img_cv.shape[2] == 4:
            pil = Image.fromarray(cv2.cvtColor(img_cv, cv2.COLOR_BGRA2RGBA))
        else:
            pil = Image.fromarray(cv2.cvtColor(img_cv, cv2.COLOR_BGR2RGB))
        fmt = cfg.fmt.upper()
        if fmt in ("JPEG", "JPG"):
            pil = pil.convert("RGB")
            pil.save(out_path, "JPEG", quality=int(cfg.jpeg_q))
        else:
            pil.save(out_path, fmt)
        return True
    except:
        logger.exception("Ошибка при сохранении изображения %s", out_path)
        return False

def process_image(in_path: Path, out_path: Path, cfg: ProcessingConfig, use_sam=True, use_onnx=True) -> Tuple[bool, str]:
    try:
        pil = Image.open(in_path)
        pil_rgb = pil.convert("RGBA") if pil.mode in ("RGBA", "LA") else pil.convert("RGB")
        image_np = np.array(pil_rgb.convert("RGB"))
        bg_mask = None
        wm_mask = None
        if cfg.auto_ai:
            try:
                bg_mask, wm_mask = auto_detect_background_and_watermark(image_np, cfg, use_sam=use_sam, use_onnx=use_onnx)
            except:
                logger.exception("auto_detect failed")
        if bg_mask is None and cfg.remove_bg:
            try:
                bg_mask = remove_background_rembg(pil_rgb, cfg)
            except:
                pass
        if bg_mask is None and (use_sam or use_onnx):
            masks = []
            try:
                if use_sam and HAS_SAM:
                    masks.append(segment_with_sam(pil_rgb))
            except:
                pass
            try:
                if use_onnx and onnx_session:
                    masks.append(segment_with_onnx(pil_rgb))
            except:
                pass
            if masks:
                fg = combine_masks(masks)
                bg_mask = cv2.bitwise_not((fg > 0).astype(np.uint8) * 255)
        if wm_mask is None and cfg.remove_wm:
            try:
                _, wmh = auto_detect_background_and_watermark(image_np, cfg, use_sam=use_sam, use_onnx=use_onnx)
                if wmh is not None:
                    wm_mask = wmh
            except:
                pass
        img_cv = np.array(pil_rgb)
        if img_cv.ndim == 2:
            img_cv = cv2.cvtColor(img_cv, cv2.COLOR_GRAY2BGR)
        elif img_cv.shape[2] == 4:
            img_cv = cv2.cvtColor(img_cv, cv2.COLOR_RGBA2BGRA)
        elif img_cv.shape[2] == 3:
            img_cv = cv2.cvtColor(img_cv, cv2.COLOR_RGB2BGR)
        if cfg.remove_bg and bg_mask is not None and bg_mask.sum() > 0:
            try:
                if img_cv.shape[2] == 3:
                    img_cv = cv2.cvtColor(img_cv, cv2.COLOR_BGR2BGRA)
                alpha = img_cv[..., 3]
                new_alpha = np.where(bg_mask > 0, 0, 255).astype(np.uint8)
                new_alpha = np.minimum(alpha, new_alpha)
                img_cv[..., 3] = new_alpha
                logger.info("Applied background mask, bg pixels=%d", int(np.count_nonzero(new_alpha==0)))
            except:
                logger.exception("apply bg mask failed")
        if cfg.remove_wm and wm_mask is not None and wm_mask.sum() > 0:
            try:
                wm_m = (wm_mask > 0).astype(np.uint8)
                img_cv = remove_watermark(img_cv, wm_m, cfg.wm_params)
                logger.info("Applied watermark removal, wm pixels=%d", int(np.count_nonzero(wm_m)))
            except:
                logger.exception("apply wm failed")
        out_final = out_path.with_suffix("." + cfg.fmt.lower())
        if save_cv_image(img_cv, out_final, cfg):
            return True, ""
        return False, f"Ошибка сохранения {out_final}"
    except UnidentifiedImageError:
        return False, f"Неопределённое изображение: {in_path.name}"
    except:
        logger.exception("Обработка изображения сбой: %s", in_path)
        return False, "Ошибка обработки"

def validate_ext(p: Path) -> bool:
    return p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}

def process_batch(input_dir: Path, output_dir: Path, cfg: ProcessingConfig, max_workers=4, use_sam=True, use_onnx=True):
    ensure_dir(input_dir)
    ensure_dir(output_dir)
    files = [p for p in sorted(input_dir.iterdir()) if p.is_file() and validate_ext(p)]
    results = []
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(process_image, p, output_dir / p.stem, cfg, use_sam, use_onnx): p for p in files}
        for f in concurrent.futures.as_completed(futures):
            p = futures[f]
            try:
                ok, msg = f.result()
                results.append((p, ok, msg))
            except:
                results.append((p, False, "Exception"))
    return results

def run_cli(argv=None):
    parser = argparse.ArgumentParser(description="Photo Processor Auto-AI")
    parser.add_argument("--input", type=Path, default=Path("./input"))
    parser.add_argument("--output", type=Path, default=Path("./output"))
    parser.add_argument("--remove_bg", action="store_true")
    parser.add_argument("--remove_wm", action="store_true")
    parser.add_argument("--auto_ai", action="store_true", help="Автоматическое обнаружение фона и водяных знаков")
    parser.add_argument("--use_sam", action="store_true")
    parser.add_argument("--use_onnx", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    cfg = ProcessingConfig(inp=args.input, outp=args.output)
    cfg.remove_bg = args.remove_bg
    cfg.remove_wm = args.remove_wm
    cfg.auto_ai = args.auto_ai
    results = process_batch(cfg.inp, cfg.outp, cfg, max_workers=args.workers, use_sam=args.use_sam, use_onnx=args.use_onnx)
    for p, ok, msg in results:
        print(f"{'✓' if ok else '✗'} {p.name}: {msg}")

def run_streamlit():
    if st is None:
        raise RuntimeError("Streamlit не установлен")
    cfg = ProcessingConfig()
    st.title("Photo Processor Auto-AI")
    st.sidebar.header("Options")
    remove_bg = st.sidebar.checkbox("Remove background (auto/rembg)", value=cfg.remove_bg)
    remove_wm = st.sidebar.checkbox("Remove watermark", value=cfg.remove_wm)
    auto_ai = st.sidebar.checkbox("Auto AI detect", value=cfg.auto_ai)
    use_sam = st.sidebar.checkbox("Use SAM (if available)", value=HAS_SAM and _sam_generator is not None)
    use_onnx = st.sidebar.checkbox("Use ONNX (if available)", value=onnx_session is not None)
    workers = st.sidebar.number_input("Workers", 1, 16, 4)
    uploaded = st.file_uploader("Upload images", type=["jpg", "jpeg", "png", "bmp", "tiff", "webp"], accept_multiple_files=True)
    temp_dir = None
    if uploaded:
        temp_dir = Path(tempfile.mkdtemp())
        for f in uploaded:
            (temp_dir / f.name).write_bytes(f.read())
        st.sidebar.success(f"Загружено {len(uploaded)} файлов")
    use_uploaded = st.sidebar.checkbox("Обрабатывать загруженные файлы", value=bool(uploaded))
    input_dir = temp_dir if use_uploaded and temp_dir is not None else Path(st.sidebar.text_input("Папка входа", str(cfg.inp)))
    output_dir = Path(st.sidebar.text_input("Папка выхода", str(cfg.outp)))
    if st.button("Обработать"):
        cfg_local = ProcessingConfig(inp=Path(input_dir), outp=Path(output_dir))
        cfg_local.remove_bg = remove_bg
        cfg_local.remove_wm = remove_wm
        cfg_local.auto_ai = auto_ai
        with st.spinner("Обработка..."):
            results = process_batch(cfg_local.inp, cfg_local.outp, cfg_local, max_workers=int(workers), use_sam=use_sam, use_onnx=use_onnx)
        success = sum(1 for _, ok, _ in results if ok)
        st.success(f"Готово: {success}/{len(results)}")
        for p, ok, _ in results:
            if ok:
                imgp = cfg_local.outp / f"{p.stem}.{cfg_local.fmt.lower()}"
                if imgp.exists():
                    st.image(Image.open(imgp), caption=p.name)
    if temp_dir and temp_dir.exists():
        try:
            shutil.rmtree(temp_dir)
        except:
            logger.exception("Ошибка очистки временной папки")

def main():
    if HAS_STREAMLIT and len(sys.argv) <= 1:
        run_streamlit()
    else:
        run_cli(sys.argv[1:])

if __name__ == "__main__":
    main()
