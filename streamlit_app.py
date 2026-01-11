#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
photo_processor_combined_auto_ai.py
Обновленная версия: автоматическое определение фона и водяных знаков с улучшенной точностью
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

# Импорт опциональных зависимостей
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

# Определение переменной наличия streamlit
HAS_STREAMLIT = st is not None

# Настройка логгера
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

# Пути моделей
ONNX_WM_PATH = Path("watermark_segmentation.onnx")
SAM_PRETRAIN = "facebook/sam-vit-huge"

# Загрузка ONNX модели
onnx_session = None
if ort is not None and ONNX_WM_PATH.exists():
    try:
        onnx_session = ort.InferenceSession(str(ONNX_WM_PATH))
        logger.info("Загружена ONNX модель: %s", ONNX_WM_PATH)
    except Exception:
        logger.exception("Ошибка загрузки ONNX модели")

# Ленивая загрузка SAM
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

# Создание папки при необходимости
def ensure_dir(p: Path):
    try:
        p.mkdir(parents=True, exist_ok=True)
    except:
        logger.exception("Ошибка при создании директории %s", p)

# ================== Новая функция улучшения границ и фона ====================
def improve_background_mask(image_np):
    """
    Улучшает маску фона для нечетких границ, используя предварительную обработку,
    градиенты и морфологию.
    """
    # 1. Конвертация в LAB и усиление контраста с помощью CLAHE
    lab = cv2.cvtColor(image_np, cv2.COLOR_RGB2Lab)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    l_eq = clahe.apply(l)
    lab_eq = cv2.merge([l_eq, a, b])
    img_eq = cv2.cvtColor(lab_eq, cv2.COLOR_Lab2RGB)

    # 2. Перевод в серый цвет
    gray = cv2.cvtColor(img_eq, cv2.COLOR_RGB2GRAY)

    # 3. Детектирование границ с помощью Canny
    edges = cv2.Canny(gray, threshold1=50, threshold2=150)
    edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3,3)), iterations=1)

    # 4. Цветовая кластеризация для определения фона
    a_channel = a.flatten()
    b_channel = b.flatten()
    a_b = np.stack([a_channel, b_channel], axis=1).astype(np.float32)

    try:
        _, labels, centers = cv2.kmeans(
            a_b, 2, None,
            (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0),
            10, cv2.KMEANS_PP_CENTERS
        )
        labels_img = labels.reshape(a.shape)
        center_vals = centers.flatten()
        bg_label = int(np.argmin(center_vals))
        mask_bg = (labels_img == bg_label).astype(np.uint8) * 255
    except:
        # Если кластеризация не удалась, используем всю область как фон
        mask_bg = np.ones_like(a, dtype=np.uint8) * 255

    # 5. Объединение границ и цветового фона
    combined_mask = cv2.bitwise_or(mask_bg, edges)

    # 6. Морфологическая обработка для сглаживания границ и устранения шумов
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5,5))
    combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel)
    combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_OPEN, kernel)

    return combined_mask

# ================== Остальные функции ====================

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

def detect_background_and_objects(
        image_np: np.ndarray,
        color_threshold=30,
        area_limits=(0.0005, 0.2)
    ) -> np.ndarray:
    """
    Улучшенная детекция фона на основе кластеризации и градиентов.
    Возвращает маску фона (uint8 0/255).
    """
    h, w = image_np.shape[:2]
    img_area = h * w

    # Детекция фона через кластеризацию по цвету
    lab = cv2.cvtColor(image_np, cv2.COLOR_RGB2Lab)
    l_channel = lab[:, :, 0]
    a_channel = lab[:, :, 1]
    b_channel = lab[:, :, 2]

    try:
        pixels = np.concatenate([a_channel.reshape(-1, 1), b_channel.reshape(-1, 1)], axis=1).astype(np.float32)
        _, labels, centers = cv2.kmeans(pixels, 2, None,
                                         (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0),
                                         10, cv2.KMEANS_PP_CENTERS)
        labels_image = labels.reshape(a_channel.shape)
        center_vals = centers.flatten()
        background_label = int(np.argmin(center_vals))
        mask_bg = (labels_image == background_label).astype(np.uint8) * 255
    except:
        mask_bg = np.ones_like(l_channel, dtype=np.uint8) * 255

    # Улучшение маски с помощью морфологии
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    mask_bg = cv2.morphologyEx(mask_bg, cv2.MORPH_OPEN, kernel)
    mask_bg = cv2.morphologyEx(mask_bg, cv2.MORPH_CLOSE, kernel)

    # Детекция границ, чтобы выделить объекты
    sobelx = cv2.Sobel(l_channel, cv2.CV_16S, 1, 0, ksize=3)
    sobely = cv2.Sobel(l_channel, cv2.CV_16S, 0, 1, ksize=3)
    gradient = cv2.magnitude(sobelx, sobely).astype(np.uint8)
    _, edges = cv2.threshold(gradient, 30, 255, cv2.THRESH_BINARY)
    edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=1)

    # Объединяем маски
    combined_mask = cv2.bitwise_or(mask_bg, edges)

    # Находим контуры объектов
    contours, _ = cv2.findContours(combined_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    mask_objects = np.zeros_like(l_channel, dtype=np.uint8)
    img_area = h * w
    min_area, max_area = area_limits[0] * img_area, area_limits[1] * img_area

    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area or area > max_area:
            continue
        cv2.drawContours(mask_objects, [c], -1, 255, thickness=-1)

    # Возвращаем маску фона (обратную маске объектов)
    final_bg_mask = cv2.bitwise_not(mask_objects)
    return final_bg_mask

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
    """
    Возвращает (bg_mask, wm_mask) - обе в формате uint8 0/255.
    """
    h, w = image_np.shape[:2]
    img_area = h * w

    # 1) rembg для фона
    bg_mask_candidates: List[np.ndarray] = []
    try:
        rembg_mask = remove_background_rembg(Image.fromarray(cv2.cvtColor(image_np, cv2.COLOR_RGB2RGBA)), cfg)
        if rembg_mask is not None:
            bg_mask_candidates.append(rembg_mask)
    except:
        pass

    # 2) сегментация для foreground
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
        obj_mask = detect_background_and_objects(image_np)
        if obj_mask is not None:
            fg_candidates.append(obj_mask)
    except:
        pass

    if fg_candidates:
        fg_comb = combine_masks(fg_candidates)
        bg_from_fg = cv2.bitwise_not((fg_comb > 0).astype(np.uint8) * 255)
        bg_mask_candidates.append(bg_from_fg)

    # 3) Цветовая/краевая однородность
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

    # Голосование
    if bg_mask_candidates:
        stacked = np.stack(bg_mask_candidates, axis=0)
        votes = np.sum(stacked > 0, axis=0)
        bg_mask = (votes >= 1).astype(np.uint8) * 255
    else:
        bg_mask = None

    # Водяной знак
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

        # фильтр по размеру и форме
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

    # ONNX watermark
    try:
        if onnx_session is not None:
            onnx_pred = segment_with_onnx(pil)
            if onnx_pred is not None and onnx_pred.sum() > 0:
                wm_candidates.append(onnx_pred)
                logger.info("ONNX watermark mask added")
    except:
        pass

    final_wm = combine_masks(wm_candidates) if wm_candidates else None

    # проверка на чрезмерное покрытие
    if final_wm is not None:
        wm_area = np.count_nonzero(final_wm) / 255
        if wm_area > 0.25 * img_area:
            logger.info("отбрасываем wm (слишком большая область) %.3f", wm_area/img_area)
            final_wm = None

    return bg_mask, final_wm

# ================== Процедура удаления водяного знака ====================
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

# ================== Resize ====================
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

# ================== Сохранение ====================
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

# ================== Основная обработка ====================
def process_image(in_path: Path, out_path: Path, cfg: ProcessingConfig, use_sam=True, use_onnx=True) -> Tuple[bool, str]:
    try:
        pil = Image.open(in_path)
        pil_rgb = pil.convert("RGBA") if pil.mode in ("RGBA", "LA") else pil.convert("RGB")
        image_np = np.array(pil_rgb.convert("RGB"))
        bg_mask = None
        wm_mask = None

        # Авто-ИИ обнаружение
        if cfg.auto_ai:
            try:
                bg_mask, wm_mask = auto_detect_background_and_watermark(image_np, cfg, use_sam=use_sam, use_onnx=use_onnx)
            except:
                logger.exception("auto_detect failed")

        # В случае отсутствия bg_mask, fallback на rembg или сегментацию
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

        # Водо- и фоновые маски для watermark
        if wm_mask is None and cfg.remove_wm:
            try:
                _, wmh = auto_detect_background_and_watermark(image_np, cfg, use_sam=use_sam, use_onnx=use_onnx)
                if wmh is not None:
                    wm_mask = wmh
            except:
                pass

        # Преобразование для inpaint и сохранения
        img_cv = np.array(pil_rgb)
        if img_cv.ndim == 2:
            img_cv = cv2.cvtColor(img_cv, cv2.COLOR_GRAY2BGR)
        elif img_cv.shape[2] == 4:
            img_cv = cv2.cvtColor(img_cv, cv2.COLOR_RGBA2BGRA)
        elif img_cv.shape[2] == 3:
            img_cv = cv2.cvtColor(img_cv, cv2.COLOR_RGB2BGR)

        # Применение фона
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

        # Удаление водяного знака
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

# ================== Блок batch обработки ====================
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

# ================== CLI и запуск ====================
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
