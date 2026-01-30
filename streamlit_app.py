import streamlit as st
import requests
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import time
import re
import json
import random
from collections import Counter
import numpy as np
from io import BytesIO
import xlsxwriter
from datetime import datetime, timedelta
import hashlib
from typing import List, Dict, Optional, Tuple, Any, Set
import logging
from fake_useragent import UserAgent
import yaml
import sqlite3
from pathlib import Path
import pickle

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== КОНФИГУРАЦИЯ ====================

class Config:
    """Конфигурация парсера автозапчастей"""
    
    def __init__(self, config_file: str = "config_auto.yaml"):
        self.config_file = config_file
        self.default_config = {
            'parsing': {
                'delay_min': 5.5,
                'delay_max': 8.0,
                'max_workers': 3,
                'use_proxies': False,
                'max_retries': 3,
                'timeout': 30,
            },
            'export': {
                'default_format': 'excel',
                'include_stats': True,
                'include_charts': True,
            },
            'api': {
                'cache_ttl': 7200,  # 2 часа для автозапчастей
                'enable_cache': True,
            },
            'auto_parts': {
                'categories': {
                    'engine': 8777,      # Двигатель
                    'transmission': 8778, # Трансмиссия
                    'brakes': 8779,      # Тормозная система
                    'suspension': 8780,  # Подвеска
                    'electrical': 8781,  # Электрика
                    'body': 8782,        # Кузов
                    'interior': 8783,    # Салон
                    'oils': 8784,        # Технические жидкости
                    'filters': 8785,     # Фильтры
                    'lighting': 8786,    # Освещение
                },
                'brands_priority': [
                    'Bosch', 'Mann', 'Febi', 'Bilstein', 'KYB',
                    'Sachs', 'Luk', 'Valeo', 'ATE', 'TRW',
                    'Delphi', 'Denso', 'NGK', 'Mobil', 'Castrol',
                    'Shell', 'Motul', 'ZIC', 'Liqui Moly'
                ],
                'car_brands': [
                    'Audi', 'BMW', 'Mercedes', 'Volkswagen', 'Toyota',
                    'Ford', 'Chevrolet', 'Hyundai', 'Kia', 'Nissan',
                    'Mazda', 'Honda', 'Mitsubishi', 'Subaru', 'Renault',
                    'Peugeot', 'Citroen', 'Skoda', 'Volvo', 'Opel'
                ]
            }
        }
        
        self.config = self.load_config()
    
    def load_config(self) -> Dict:
        """Загрузка конфигурации из файла"""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                return self.deep_update(self.default_config, config)
        except FileNotFoundError:
            logger.warning(f"Конфиг файл {self.config_file} не найден, используются значения по умолчанию")
            return self.default_config
    
    def deep_update(self, d: Dict, u: Dict) -> Dict:
        """Рекурсивное обновление словаря"""
        for k, v in u.items():
            if isinstance(v, dict) and k in d and isinstance(d[k], dict):
                d[k] = self.deep_update(d[k], v)
            else:
                d[k] = v
        return d

# ==================== КЕШИРОВАНИЕ ====================

class CacheManager:
    """Менеджер кэширования для автозапчастей"""
    
    def __init__(self, cache_dir: str = "cache_auto", ttl: int = 7200):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.ttl = ttl
        self.memory_cache = {}
        
    def get_key(self, endpoint: str, params: Dict) -> str:
        """Генерация ключа кэша"""
        key_str = f"{endpoint}:{json.dumps(params, sort_keys=True)}"
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def get(self, key: str) -> Optional[Dict]:
        """Получение данных из кэша"""
        if key in self.memory_cache:
            cached_data = self.memory_cache[key]
            if time.time() - cached_data['timestamp'] < self.ttl:
                return cached_data['data']
        
        cache_file = self.cache_dir / f"{key}.pkl"
        if cache_file.exists():
            try:
                with open(cache_file, 'rb') as f:
                    cached_data = pickle.load(f)
                if time.time() - cached_data['timestamp'] < self.ttl:
                    self.memory_cache[key] = cached_data
                    return cached_data['data']
            except Exception as e:
                logger.error(f"Ошибка чтения кэша: {e}")
        
        return None
    
    def set(self, key: str, data: Dict):
        """Сохранение данных в кэш"""
        cached_data = {
            'data': data,
            'timestamp': time.time()
        }
        
        self.memory_cache[key] = cached_data
        
        cache_file = self.cache_dir / f"{key}.pkl"
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(cached_data, f)
        except Exception as e:
            logger.error(f"Ошибка сохранения кэша: {e}")

# ==================== КЛАСС ДЛЯ АВТОЗАПЧАСТЕЙ ====================

class AutoPartsNormalizer:
    """Нормализация характеристик автозапчастей"""
    
    # Словарь нормализации для автозапчастей
    CHAR_NORMALIZATION = {
        # Общие
        'Бренд': 'brand',
        'Производитель': 'manufacturer',
        'Артикул': 'article',
        'Артикул производителя': 'vendor_code',
        'ОЕМ номер': 'oem_number',
        'Оригинальный номер': 'original_number',
        'Аналог': 'analog',
        'Аналогичные номера': 'analog_numbers',
        'Страна': 'country',
        'Страна производства': 'country',
        
        # Применяемость
        'Марка автомобиля': 'car_brand',
        'Модель автомобиля': 'car_model',
        'Год выпуска': 'production_year',
        'Годы выпуска': 'production_years',
        'Двигатель': 'engine',
        'Объем двигателя': 'engine_capacity',
        'Мощность': 'engine_power',
        'Топливо': 'fuel_type',
        'Тип КПП': 'transmission_type',
        'Привод': 'drive_type',
        'Кузов': 'body_type',
        'VIN': 'vin_code',
        
        # Характеристики запчастей
        'Тип запчасти': 'part_type',
        'Категория': 'category',
        'Подкатегория': 'subcategory',
        'Назначение': 'purpose',
        'Материал': 'material',
        'Цвет': 'color',
        'Размер': 'size',
        'Вес': 'weight',
        'Длина': 'length',
        'Ширина': 'width',
        'Высота': 'height',
        'Диаметр': 'diameter',
        'Толщина': 'thickness',
        
        # Упаковка
        'Количество в упаковке': 'quantity_per_pack',
        'Единица измерения': 'unit',
        'Упаковка': 'packaging',
        
        # Гарантия и сроки
        'Гарантия': 'warranty',
        'Срок службы': 'lifespan',
        'Срок годности': 'expiry_date',
        
        # Совместимость
        'Совместимость': 'compatibility',
        'Заменяет': 'replaces',
        'Взаимозаменяемость': 'interchangeability',
    }
    
    # Ключевые слова для определения категории
    CATEGORY_KEYWORDS = {
        'двигатель': ['поршень', 'кольцо', 'вкладыш', 'распредвал', 'коленвал', 'гбц', 'головка', 'клапан', 'ремень грм'],
        'трансмиссия': ['сцепление', 'диск сцепления', 'коробка передач', 'акпп', 'мкпп', 'кардан', 'шрус', 'дифференциал'],
        'тормоза': ['тормозной диск', 'тормозная колодка', 'тормозной суппорт', 'тормозной шланг', 'тормозная жидкость'],
        'подвеска': ['амортизатор', 'стойка', 'пружина', 'рычаг', 'сайлентблок', 'шаровой', 'ступица', 'подшипник'],
        'электрика': ['генератор', 'стартер', 'аккумулятор', 'катушка зажигания', 'свеча', 'датчик', 'реле', 'проводка'],
        'кузов': ['крыло', 'капот', 'дверь', 'бампер', 'порог', 'стекло', 'зеркало', 'ручка'],
        'салон': ['сиденье', 'руль', 'приборная панель', 'коврик', 'обшивка', 'потолок'],
        'фильтры': ['воздушный фильтр', 'масляный фильтр', 'топливный фильтр', 'салонный фильтр'],
        'масла': ['моторное масло', 'трансмиссионное масло', 'тормозная жидкость', 'антифриз', 'омывайка'],
        'освещение': ['фара', 'фонарь', 'лампа', 'блок-фара', 'противотуманка', 'поворотник'],
    }
    
    @staticmethod
    def normalize_key(key: str) -> str:
        """Нормализация ключа характеристики для автозапчастей"""
        if not key:
            return ''
        
        key_lower = key.strip().lower()
        key_clean = re.sub(r'[^\w\s]', '', key_lower)
        
        # Ищем в словаре нормализации
        for ru_key, en_key in AutoPartsNormalizer.CHAR_NORMALIZATION.items():
            if ru_key.lower() in key_lower:
                return en_key
        
        return key_clean.replace(' ', '_')
    
    @staticmethod
    def normalize_value(value: str) -> Any:
        """Нормализация значения"""
        if not value:
            return ''
        
        value = str(value).strip()
        
        # Числовые значения
        if re.match(r'^\d+$', value):
            return int(value)
        elif re.match(r'^\d+\.\d+$', value):
            return float(value)
        
        # Диапазоны лет (например: 2010-2015)
        if re.match(r'^\d{4}\s*[-–]\s*\d{4}$', value):
            years = re.findall(r'\d{4}', value)
            return f"{years[0]}-{years[1]}"
        
        # Объем двигателя (например: 1.6 л)
        if re.match(r'^\d+\.?\d*\s*л$', value, re.IGNORECASE):
            return float(re.findall(r'\d+\.?\d*', value)[0])
        
        # Мощность (например: 150 л.с.)
        if re.match(r'^\d+\s*л\.?с\.?$', value, re.IGNORECASE):
            return int(re.findall(r'\d+', value)[0])
        
        return value
    
    @staticmethod
    def detect_category(name: str, characteristics: Dict) -> str:
        """Определение категории автозапчасти"""
        text_for_analysis = name.lower()
        
        # Добавляем характеристики для анализа
        for char_value in characteristics.values():
            if isinstance(char_value, str):
                text_for_analysis += ' ' + char_value.lower()
        
        # Ищем ключевые слова
        for category, keywords in AutoPartsNormalizer.CATEGORY_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text_for_analysis:
                    return category
        
        return 'other'

# ==================== ПАРСЕР АВТОЗАПЧАСТЕЙ ====================

class AutoPartsParser:
    """Специализированный парсер для автозапчастей"""
    
    def __init__(self, request_manager, config: Dict):
        self.request_manager = request_manager
        self.config = config
        self.normalizer = AutoPartsNormalizer()
        self.ua = UserAgent()
    
    def parse_auto_part(self, product_id: str) -> Dict:
        """Парсинг одной автозапчасти"""
        result = {
            'product_id': product_id,
            'parse_timestamp': datetime.now().isoformat(),
            'parse_status': 'success',
            'part_type': 'auto_part'
        }
        
        try:
            # Получаем основные данные
            main_data = self._get_main_data(product_id)
            if not main_data:
                result['parse_status'] = 'failed_main_data'
                return result
            
            result.update(main_data)
            
            # Детальная информация
            details = self._get_detailed_info(product_id)
            if details:
                result.update(details)
            
            # Определяем категорию
            if 'name' in result:
                characteristics = {k: v for k, v in result.items() 
                                 if isinstance(v, str) and k not in ['name', 'brand', 'parse_status']}
                result['auto_category'] = self.normalizer.detect_category(
                    result['name'], characteristics
                )
            
            # Нормализуем характеристики
            result = self._normalize_characteristics(result)
            
            # Извлекаем информацию о совместимости
            result.update(self._extract_compatibility_info(result))
            
        except Exception as e:
            logger.error(f"Ошибка парсинга автозапчасти {product_id}: {e}")
            result.update({
                'parse_status': 'error',
                'error_message': str(e)
            })
        
        return result
    
    def _get_main_data(self, product_id: str) -> Dict:
        """Получение основных данных товара"""
        url = "https://card.wb.ru/cards/v2/detail"
        
        params = {
            'appType': 1,
            'curr': 'rub',
            'dest': -1257786,
            'spp': 30,
            'nm': product_id,
        }
        
        response = self.request_manager.make_request(url, params=params)
        if not response or response.status_code != 200:
            return {}
        
        try:
            data = response.json()
            product = data.get('data', {}).get('products', [{}])[0]
            
            result = {
                'wb_id': product.get('id'),
                'name': product.get('name', ''),
                'brand': product.get('brand', ''),
                'priceU': product.get('priceU', 0) / 100,
                'salePriceU': product.get('salePriceU', 0) / 100,
                'basicPriceU': product.get('basicPriceU', 0) / 100,
                'sale': product.get('sale', 0),
                'rating': product.get('rating', 0),
                'feedbacks': product.get('feedbacks', 0),
                'supplier': product.get('supplier', ''),
                'supplierRating': product.get('supplierRating', 0),
                'supplierId': product.get('supplierId'),
                'pics': product.get('pics', 0),
                'url': f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx",
            }
            
            # Характеристики из options
            characteristics = {}
            for opt in product.get('options', []):
                name = opt.get('name', '').strip()
                value = opt.get('value', '').strip()
                if name and value:
                    characteristics[name] = value
            
            result['characteristics_raw'] = json.dumps(characteristics, ensure_ascii=False)
            
            # Категории
            categories = product.get('categoryTree', [])
            if categories:
                result['wb_category'] = categories[-1].get('name', '') if categories else ''
                result['wb_category_id'] = categories[-1].get('id') if categories else ''
            
            return result
            
        except Exception as e:
            logger.error(f"Ошибка получения основных данных: {e}")
            return {}
    
    def _get_detailed_info(self, product_id: str) -> Dict:
        """Получение детальной информации"""
        # Используем разные методы для получения детальной информации
        details = {}
        
        # Метод 1: через страницу товара
        page_info = self._parse_product_page(product_id)
        if page_info:
            details.update(page_info)
        
        # Метод 2: через API характеристик
        char_info = self._get_characteristics_api(product_id)
        if char_info:
            details.update(char_info)
        
        return details
    
    def _parse_product_page(self, product_id: str) -> Dict:
        """Парсинг страницы товара"""
        url = f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx"
        
        headers = {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        
        response = self.request_manager.make_request(url, headers=headers, use_cache=False)
        if not response or response.status_code != 200:
            return {}
        
        try:
            html_content = response.text
            
            # Ищем JSON данные в странице
            json_patterns = [
                r'window\.__NUXT__\s*=\s*({.*?});',
                r'"product"\s*:\s*({.*?})',
                r'data:\s*({.*?}),'
            ]
            
            for pattern in json_patterns:
                match = re.search(pattern, html_content, re.DOTALL)
                if match:
                    try:
                        json_data = json.loads(match.group(1))
                        return self._extract_from_page_json(json_data)
                    except:
                        continue
            
            # Парсинг характеристик из HTML
            characteristics = self._parse_characteristics_from_html(html_content)
            if characteristics:
                return {'page_characteristics': json.dumps(characteristics, ensure_ascii=False)}
            
        except Exception as e:
            logger.error(f"Ошибка парсинга страницы: {e}")
        
        return {}
    
    def _parse_characteristics_from_html(self, html: str) -> Dict:
        """Парсинг характеристик из HTML"""
        characteristics = {}
        
        # Простой парсинг таблицы характеристик
        table_pattern = r'<table[^>]*class="[^"]*characteristics[^"]*"[^>]*>.*?</table>'
        match = re.search(table_pattern, html, re.DOTALL | re.IGNORECASE)
        
        if match:
            table_html = match.group(0)
            # Ищем строки таблицы
            row_pattern = r'<tr[^>]*>.*?</tr>'
            rows = re.findall(row_pattern, table_html, re.DOTALL)
            
            for row in rows:
                # Ищем ячейки
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                if len(cells) >= 2:
                    key = re.sub(r'<[^>]+>', '', cells[0]).strip()
                    value = re.sub(r'<[^>]+>', '', cells[1]).strip()
                    if key and value:
                        characteristics[key] = value
        
        return characteristics
    
    def _extract_from_page_json(self, json_data: Dict) -> Dict:
        """Извлечение данных из JSON страницы"""
        result = {}
        
        try:
            # Разные структуры JSON
            if 'product' in json_data:
                product_data = json_data['product']
            elif 'data' in json_data:
                product_data = json_data['data']
            else:
                product_data = json_data
            
            # Извлекаем характеристики
            if 'options' in product_data:
                for opt in product_data['options']:
                    if 'name' in opt and 'value' in opt:
                        key = str(opt['name']).strip()
                        value = str(opt['value']).strip()
                        norm_key = self.normalizer.normalize_key(key)
                        result[norm_key] = value
            
            # Дополнительная информация
            if 'description' in product_data:
                result['description'] = product_data['description']
            
        except Exception as e:
            logger.error(f"Ошибка извлечения из JSON: {e}")
        
        return result
    
    def _get_characteristics_api(self, product_id: str) -> Dict:
        """Получение характеристик через API"""
        url = "https://wbx-content-v2.wbstatic.net/ru"
        
        # Пробуем разные варианты API
        endpoints = [
            f"/{product_id[:3]}/{product_id}.json",
            f"/{product_id[:2]}/{product_id[2:4]}/{product_id}.json",
            f"/{product_id}.json"
        ]
        
        for endpoint in endpoints:
            try:
                full_url = url + endpoint
                response = self.request_manager.make_request(full_url)
                if response and response.status_code == 200:
                    data = response.json()
                    return self._parse_characteristics_api(data)
            except:
                continue
        
        return {}
    
    def _parse_characteristics_api(self, data: Dict) -> Dict:
        """Парсинг характеристик из API"""
        result = {}
        
        try:
            if 'options' in data:
                for opt in data['options']:
                    if 'name' in opt and 'value' in opt:
                        key = str(opt['name']).strip()
                        value = str(opt['value']).strip()
                        norm_key = self.normalizer.normalize_key(key)
                        result[norm_key] = value
            
            # Дополнительные поля
            if 'imt_name' in data:
                result['full_name'] = data['imt_name']
            if 'description' in data:
                result['detailed_description'] = data['description']
            
        except Exception as e:
            logger.error(f"Ошибка парсинга API характеристик: {e}")
        
        return result
    
    def _normalize_characteristics(self, data: Dict) -> Dict:
        """Нормализация всех характеристик"""
        normalized = {}
        
        # Обрабатываем characteristics_raw
        if 'characteristics_raw' in data:
            try:
                chars = json.loads(data['characteristics_raw'])
                for char_key, char_value in chars.items():
                    norm_key = self.normalizer.normalize_key(char_key)
                    norm_value = self.normalizer.normalize_value(char_value)
                    normalized[norm_key] = norm_value
            except:
                pass
        
        # Обрабатываем page_characteristics
        if 'page_characteristics' in data:
            try:
                page_chars = json.loads(data['page_characteristics'])
                for char_key, char_value in page_chars.items():
                    norm_key = self.normalizer.normalize_key(char_key)
                    norm_value = self.normalizer.normalize_value(char_value)
                    normalized[norm_key] = norm_value
            except:
                pass
        
        # Остальные поля
        for key, value in data.items():
            if key not in ['characteristics_raw', 'page_characteristics']:
                norm_key = self.normalizer.normalize_key(key)
                norm_value = self.normalizer.normalize_value(value)
                normalized[norm_key] = norm_value
        
        return normalized
    
    def _extract_compatibility_info(self, data: Dict) -> Dict:
        """Извлечение информации о совместимости"""
        result = {}
        
        # Анализируем название и характеристики
        text_for_analysis = ''
        
        if 'name' in data:
            text_for_analysis += ' ' + data['name'].lower()
        
        if 'full_name' in data:
            text_for_analysis += ' ' + data['full_name'].lower()
        
        if 'description' in data:
            text_for_analysis += ' ' + data['description'].lower()
        
        # Ищем марки автомобилей
        car_brands = self.config['auto_parts']['car_brands']
        found_brands = []
        
        for brand in car_brands:
            if brand.lower() in text_for_analysis:
                found_brands.append(brand)
        
        if found_brands:
            result['compatible_brands'] = '; '.join(found_brands)
        
        # Ищем модели (шаблоны: Audi A4, BMW X5 и т.д.)
        model_pattern = r'([A-Z][a-z]+)\s+([A-Z0-9][\w\s]*)'
        matches = re.findall(model_pattern, text_for_analysis.title())
        if matches:
            models = [f"{brand} {model}" for brand, model in matches]
            result['compatible_models'] = '; '.join(models)
        
        # Ищем годы выпуска
        year_pattern = r'(?:19|20)\d{2}'
        years = re.findall(year_pattern, text_for_analysis)
        if years:
            unique_years = sorted(set(years))
            result['compatible_years'] = '; '.join(unique_years)
        
        # Ищем объем двигателя
        engine_pattern = r'(\d+\.?\d*)\s*[LlЛл]'
        engines = re.findall(engine_pattern, text_for_analysis)
        if engines:
            result['engine_capacities'] = '; '.join(engines)
        
        return result

# ==================== КЛИЕНТ ДЛЯ АВТОЗАПЧАСТЕЙ ====================

class AutoPartsClient:
    """Клиент для работы с автозапчастями Wildberries"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.request_manager = RequestManager(config)
        self.parser = AutoPartsParser(self.request_manager, config)
    
    def search_auto_parts(self, query: str, category: str = None, 
                         min_price: int = None, max_price: int = None,
                         page: int = 1, limit: int = 100) -> pd.DataFrame:
        """Поиск автозапчастей"""
        
        # Добавляем ключевые слова для автозапчастей
        auto_query = query
        if not any(word in query.lower() for word in ['запчасть', 'деталь', 'комплект', 'комплектующие']):
            auto_query = f"{query} запчасть"
        
        # Используем категорию
        category_id = None
        if category and category in self.config['auto_parts']['categories']:
            category_id = self.config['auto_parts']['categories'][category]
        
        # Используем обычный поиск
        url = "https://search.wb.ru/exactmatch/ru/common/v4/search"
        
        params = {
            'appType': 1,
            'curr': 'rub',
            'dest': -1257786,
            'lang': 'ru',
            'locale': 'ru',
            'page': page,
            'query': auto_query,
            'resultset': 'catalog',
            'sort': 'popular',
            'spp': 30,
            'suppressSpellcheck': 'false',
        }
        
        if category_id:
            params['subject'] = category_id
        
        params['limit'] = min(limit, 300)
        
        response = self.request_manager.make_request(url, params=params)
        if not response or response.status_code != 200:
            return pd.DataFrame()
        
        return self._parse_search_results(response.json())
    
    def _parse_search_results(self, data: Dict) -> pd.DataFrame:
        """Парсинг результатов поиска"""
        try:
            products = data.get('data', {}).get('products', [])
            if not products:
                return pd.DataFrame()
            
            items = []
            for p in products:
                try:
                    item = self._parse_product(p)
                    items.append(item)
                except Exception as e:
                    logger.error(f"Ошибка парсинга товара: {e}")
                    continue
            
            return pd.DataFrame(items)
            
        except Exception as e:
            logger.error(f"Ошибка парсинга результатов: {e}")
            return pd.DataFrame()
    
    def _parse_product(self, product: Dict) -> Dict:
        """Парсинг одного товара"""
        # Цвета
        colors = []
        for color in product.get('colors', []):
            colors.append(color.get('name', ''))
        
        # Размеры
        sizes = []
        for size_data in product.get('sizes', []):
            size_name = size_data.get('name', '')
            if size_name:
                sizes.append(size_name)
        
        # Характеристики
        characteristics = {}
        for opt in product.get('options', []):
            name = opt.get('name', '').strip()
            value = opt.get('value', '').strip()
            if name and value:
                characteristics[name] = value
        
        return {
            'id': product.get('id'),
            'name': product.get('name', ''),
            'brand': product.get('brand', ''),
            'price': product.get('priceU', 0) / 100,
            'sale_price': product.get('salePriceU', 0) / 100,
            'sale': product.get('sale', 0),
            'rating': product.get('rating', 0),
            'feedbacks': product.get('feedbacks', 0),
            'supplier': product.get('supplier', ''),
            'supplierId': product.get('supplierId'),
            'pics': product.get('pics', 0),
            'colors': '; '.join(colors),
            'sizes': '; '.join(sizes),
            'url': f"https://www.wildberries.ru/catalog/{product.get('id')}/detail.aspx",
            'characteristics': json.dumps(characteristics, ensure_ascii=False),
        }
    
    def get_brands_analysis(self, df: pd.DataFrame) -> Dict:
        """Анализ брендов в данных"""
        if df.empty or 'brand' not in df.columns:
            return {}
        
        analysis = {
            'total_brands': df['brand'].nunique(),
            'top_brands': [],
            'brand_price_stats': {}
        }
        
        # Топ брендов по количеству
        top_brands = df['brand'].value_counts().head(20)
        analysis['top_brands'] = top_brands.to_dict()
        
        # Статистика цен по брендам
        if 'price' in df.columns:
            for brand in top_brands.index:
                brand_data = df[df['brand'] == brand]
                if not brand_data.empty:
                    analysis['brand_price_stats'][brand] = {
                        'count': len(brand_data),
                        'avg_price': brand_data['price'].mean(),
                        'min_price': brand_data['price'].min(),
                        'max_price': brand_data['price'].max()
                    }
        
        return analysis

# ==================== STREAMLIT ИНТЕРФЕЙС ====================

class RequestManager:
    """Упрощенный менеджер запросов"""
    
    def __init__(self, config: Dict):
        self.ua = UserAgent()
        self.session = requests.Session()
        self.session.headers.update(self._get_base_headers())
        self.delay_range = (config['parsing']['delay_min'], config['parsing']['delay_max'])
        self.max_retries = config['parsing']['max_retries']
        self.timeout = config['parsing']['timeout']
        self.cache_manager = CacheManager() if config['api']['enable_cache'] else None
        
    def _get_base_headers(self) -> Dict:
        return {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'User-Agent': self.ua.random,
            'Referer': 'https://www.wildberries.ru/',
        }
    
    def make_request(self, url: str, method: str = 'GET', 
                    use_cache: bool = True, **kwargs) -> Optional[requests.Response]:
        """Выполнение запроса"""
        
        # Проверяем кэш
        if use_cache and self.cache_manager and method == 'GET':
            params = kwargs.get('params', {})
            cache_key = self.cache_manager.get_key(url, params)
            cached_data = self.cache_manager.get(cache_key)
            if cached_data:
                logger.debug(f"Используем кэш для {url}")
                response = requests.Response()
                response.status_code = cached_data['status_code']
                response.headers = cached_data['headers']
                response._content = cached_data['content']
                response.encoding = cached_data['encoding']
                return response
        
        # Задержка
        time.sleep(random.uniform(*self.delay_range))
        
        # Выполняем запрос
        headers = self._get_base_headers()
        headers.update(kwargs.pop('headers', {}))
        
        for attempt in range(self.max_retries):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    timeout=self.timeout,
                    **kwargs
                )
                
                if response.status_code == 200:
                    # Сохраняем в кэш
                    if use_cache and self.cache_manager and method == 'GET':
                        params = kwargs.get('params', {})
                        cache_key = self.cache_manager.get_key(url, params)
                        self.cache_manager.set(cache_key, {
                            'status_code': response.status_code,
                            'headers': dict(response.headers),
                            'content': response.content,
                            'encoding': response.encoding,
                        })
                    
                    return response
                else:
                    logger.warning(f"Статус {response.status_code} для {url}")
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"Ошибка запроса (попытка {attempt + 1}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(random.uniform(2, 5))
        
        return None

def main():
    st.set_page_config(
        page_title="Парсинг автозапчастей Wildberries",
        layout="wide",
        initial_sidebar_state="expanded",
        page_icon="🚗"
    )
    
    # Стили
    st.markdown("""
    <style>
    .stProgress > div > div > div > div {
        background-color: #FF6B35;
    }
    .metric-container {
        background-color: #f8f9fa;
        padding: 15px;
        border-radius: 10px;
        border-left: 5px solid #FF6B35;
        margin: 5px 0;
    }
    .car-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 20px;
        border-radius: 10px;
        margin: 10px 0;
    }
    .part-card {
        background: #f0f8ff;
        border: 2px solid #4A90E2;
        border-radius: 8px;
        padding: 15px;
        margin: 10px 0;
    }
    </style>
    """, unsafe_allow_html=True)
    
    st.title("🚗 Парсинг автозапчастей Wildberries")
    st.markdown("---")
    
    # Инициализация
    if 'config' not in st.session_state:
        st.session_state.config = Config().config
    
    if 'auto_client' not in st.session_state:
        st.session_state.auto_client = AutoPartsClient(st.session_state.config)
    
    if 'parsed_data' not in st.session_state:
        st.session_state.parsed_data = pd.DataFrame()
    
    if 'analysis_results' not in st.session_state:
        st.session_state.analysis_results = {}
    
    # Боковая панель
    with st.sidebar:
        st.header("⚙️ Настройки парсинга")
        
        search_mode = st.radio(
            "Режим поиска",
            ["По запросу", "По категории", "По артикулам", "Анализ брендов"],
            help="Выберите способ поиска автозапчастей"
        )
        
        st.subheader("🔧 Параметры поиска")
        
        if search_mode == "По запросу":
            query = st.text_input("Запрос для поиска", value="тормозные колодки")
            
            col1, col2 = st.columns(2)
            with col1:
                pages = st.slider("Страниц", 1, 10, 2)
            with col2:
                per_page = st.select_slider("На странице", [50, 100, 150], 100)
        
        elif search_mode == "По категории":
            categories = list(st.session_state.config['auto_parts']['categories'].keys())
            category = st.selectbox("Категория запчастей", categories)
            
            col1, col2 = st.columns(2)
            with col1:
                min_price = st.number_input("Цена от, руб", 0, 1000000, 0)
            with col2:
                max_price = st.number_input("Цена до, руб", 0, 1000000, 50000)
            
            query = st.text_input("Дополнительный запрос (необязательно)", value="")
        
        elif search_mode == "По артикулам":
            articles_input = st.text_area(
                "Артикулы Wildberries",
                placeholder="Введите артикулы через запятую или каждый с новой строки\n\nПример:\n12345678\n87654321",
                height=150
            )
            articles = [art.strip() for art in articles_input.replace('\n', ',').split(',') if art.strip()]
            
            if articles:
                st.info(f"Найдено артикулов: {len(articles)}")
        
        else:  # Анализ брендов
            brand_query = st.text_input("Бренд для анализа", value="Bosch")
            analysis_depth = st.select_slider(
                "Глубина анализа",
                ["Базовый", "Средний", "Полный"],
                "Средний"
            )
        
        st.subheader("⚡ Параметры парсинга")
        
        col1, col2 = st.columns(2)
        with col1:
            mode = st.selectbox(
                "Скорость",
                ["Медленный", "Сбалансированный", "Быстрый"],
                index=1
            )
            
            if mode == "Быстрый":
                delay = (0.8, 1.5)
            elif mode == "Медленный":
                delay = (2.0, 4.0)
            else:
                delay = (1.2, 2.5)
            
            st.session_state.config['parsing']['delay_min'] = delay[0]
            st.session_state.config['parsing']['delay_max'] = delay[1]
        
        with col2:
            get_details = st.checkbox("Детальный парсинг", value=True)
            use_cache = st.checkbox("Использовать кэш", value=True)
            st.session_state.config['api']['enable_cache'] = use_cache
        
        st.subheader("💾 Экспорт")
        export_format = st.selectbox("Формат", ["Excel", "CSV", "JSON"])
        include_charts = st.checkbox("Включать графики", value=True)
    
    # Основная область
    tab1, tab2, tab3, tab4 = st.tabs(["🔍 Поиск", "📊 Анализ", "🚗 Детали", "💾 Экспорт"])
    
    with tab1:
        st.header("Поиск автозапчастей")
        
        # Отображение текущих настроек
        with st.expander("📋 Текущие настройки", expanded=False):
            cols = st.columns(3)
            with cols[0]:
                st.metric("Задержка", f"{delay[0]}-{delay[1]} сек")
            with cols[1]:
                st.metric("Режим", search_mode)
            with cols[2]:
                st.metric("Кэш", "✅ Вкл" if use_cache else "❌ Выкл")
        
        # Кнопка запуска
        col1, col2 = st.columns([3, 1])
        
        with col1:
            parse_disabled = False
            parse_label = "🚀 Начать парсинг"
            
            if search_mode == "По запросу" and 'query' in locals() and query:
                parse_label = f"🔍 Искать '{query}'"
            elif search_mode == "По категории" and 'category' in locals():
                parse_label = f"📁 Искать в '{category}'"
            elif search_mode == "По артикулам" and articles:
                parse_label = f"🔢 Парсить {len(articles)} артикулов"
            elif search_mode == "Анализ брендов" and 'brand_query' in locals() and brand_query:
                parse_label = f"📊 Анализ '{brand_query}'"
            else:
                parse_disabled = True
            
            parse_clicked = st.button(
                parse_label,
                type="primary",
                use_container_width=True,
                disabled=parse_disabled,
                key="parse_btn"
            )
        
        with col2:
            if not st.session_state.parsed_data.empty:
                if st.button("🗑️ Очистить", use_container_width=True):
                    st.session_state.parsed_data = pd.DataFrame()
                    st.session_state.analysis_results = {}
                    st.rerun()
        
        # Выполнение парсинга
        if parse_clicked:
            with st.spinner("Выполняю поиск..."):
                try:
                    if search_mode == "По запросу":
                        all_results = []
                        progress_bar = st.progress(0)
                        
                        for page in range(1, pages + 1):
                            df_page = st.session_state.auto_client.search_auto_parts(
                                query=query,
                                page=page,
                                limit=per_page
                            )
                            
                            if not df_page.empty:
                                all_results.append(df_page)
                                st.info(f"Страница {page}: {len(df_page)} запчастей")
                            
                            progress_bar.progress(page / pages)
                            time.sleep(random.uniform(*delay))
                        
                        if all_results:
                            df = pd.concat(all_results, ignore_index=True)
                            
                            # Детальный парсинг
                            if get_details and not df.empty:
                                with st.spinner("Сбор детальной информации..."):
                                    details_progress = st.progress(0)
                                    details_list = []
                                    
                                    for idx, row in df.iterrows():
                                        product_id = str(row['id'])
                                        details = st.session_state.auto_client.parser.parse_auto_part(product_id)
                                        details_list.append(details)
                                        
                                        if idx % 3 == 0:
                                            details_progress.progress((idx + 1) / len(df))
                                        
                                        time.sleep(random.uniform(*delay))
                                    
                                    details_progress.progress(1.0)
                                    
                                    # Объединяем данные
                                    if details_list:
                                        details_df = pd.DataFrame(details_list)
                                        df = pd.merge(df, details_df, left_on='id', right_on='wb_id', how='left', suffixes=('', '_detail'))
                                    
                                    # Анализ брендов
                                    analysis = st.session_state.auto_client.get_brands_analysis(df)
                                    st.session_state.analysis_results = analysis
                            
                            st.session_state.parsed_data = df
                            
                            # Показываем результаты
                            st.success(f"""
                            <div class="car-card">
                            <h4>✅ Найдено автозапчастей: {len(df)}</h4>
                            <p><strong>Статистика:</strong></p>
                            <ul>
                            <li>Уникальных брендов: {df['brand'].nunique() if 'brand' in df.columns else 0}</li>
                            <li>Средняя цена: {df['price'].mean():,.0f} ₽</li>
                            <li>Категорий определено: {df['auto_category'].nunique() if 'auto_category' in df.columns else 0}</li>
                            </ul>
                            </div>
                            """, unsafe_allow_html=True)
                        
                        else:
                            st.warning("Не найдено запчастей по данному запросу")
                    
                    elif search_mode == "По категории":
                        with st.spinner(f"Ищу в категории '{category}'..."):
                            df = st.session_state.auto_client.search_auto_parts(
                                query=query if query else category,
                                category=category,
                                page=1,
                                limit=100
                            )
                            
                            if not df.empty:
                                # Фильтрация по цене
                                if min_price > 0 or max_price < 1000000:
                                    df = df[(df['price'] >= min_price) & (df['price'] <= max_price)]
                                
                                st.session_state.parsed_data = df
                                st.success(f"Найдено {len(df)} запчастей в категории '{category}'")
                            else:
                                st.warning(f"Не найдено запчастей в категории '{category}'")
                    
                    elif search_mode == "По артикулам":
                        if not articles:
                            st.warning("Введите артикулы")
                        else:
                            all_details = []
                            progress_bar = st.progress(0)
                            
                            for idx, article in enumerate(articles):
                                clean_article = re.sub(r'\D', '', article)
                                if clean_article:
                                    try:
                                        details = st.session_state.auto_client.parser.parse_auto_part(clean_article)
                                        if details['parse_status'] == 'success':
                                            all_details.append(details)
                                            st.info(f"Артикул {clean_article}: найдено")
                                        else:
                                            st.warning(f"Артикул {clean_article}: не найден")
                                    except Exception as e:
                                        st.error(f"Ошибка артикула {clean_article}: {e}")
                                
                                progress_bar.progress((idx + 1) / len(articles))
                                time.sleep(random.uniform(*delay))
                            
                            if all_details:
                                df = pd.DataFrame(all_details)
                                st.session_state.parsed_data = df
                                st.success(f"Найдено {len(df)} запчастей из {len(articles)} артикулов")
                            else:
                                st.warning("Не найдено ни одной запчасти")
                    
                    elif search_mode == "Анализ брендов":
                        with st.spinner(f"Анализирую бренд '{brand_query}'..."):
                            # Ищем товары бренда
                            df = st.session_state.auto_client.search_auto_parts(
                                query=brand_query,
                                page=1,
                                limit=200
                            )
                            
                            if not df.empty:
                                # Фильтруем по бренду
                                df_brand = df[df['brand'].str.contains(brand_query, case=False, na=False)]
                                
                                if not df_brand.empty:
                                    # Детальный анализ
                                    analysis = st.session_state.auto_client.get_brands_analysis(df_brand)
                                    
                                    st.session_state.parsed_data = df_brand
                                    st.session_state.analysis_results = analysis
                                    
                                    st.success(f"""
                                    <div class="part-card">
                                    <h4>📊 Анализ бренда: {brand_query}</h4>
                                    <p><strong>Найдено товаров:</strong> {len(df_brand)}</p>
                                    <p><strong>Средняя цена:</strong> {df_brand['price'].mean():,.0f} ₽</p>
                                    <p><strong>Диапазон цен:</strong> {df_brand['price'].min():,.0f} - {df_brand['price'].max():,.0f} ₽</p>
                                    </div>
                                    """, unsafe_allow_html=True)
                                else:
                                    st.warning(f"Не найдено товаров бренда '{brand_query}'")
                            else:
                                st.warning("Не удалось получить данные для анализа")
                
                except Exception as e:
                    st.error(f"❌ Ошибка: {str(e)}")
                    logger.exception("Ошибка парсинга")
    
    # Отображение данных
    if not st.session_state.parsed_data.empty:
        df = st.session_state.parsed_data
        
        with tab2:
            st.header("📊 Анализ данных")
            
            if not df.empty:
                # Основные метрики
                st.subheader("📈 Ключевые показатели")
                
                metric_cols = st.columns(4)
                with metric_cols[0]:
                    st.metric("Всего запчастей", len(df))
                with metric_cols[1]:
                    if 'price' in df.columns:
                        avg_price = df['price'].mean()
                        st.metric("Средняя цена", f"{avg_price:,.0f} ₽")
                with metric_cols[2]:
                    if 'brand' in df.columns:
                        unique_brands = df['brand'].nunique()
                        st.metric("Брендов", unique_brands)
                with metric_cols[3]:
                    if 'rating' in df.columns:
                        avg_rating = df['rating'].mean()
                        st.metric("Средний рейтинг", f"{avg_rating:.2f}")
                
                # Визуализации
                if len(df) > 1:
                    st.subheader("📊 Визуализация данных")
                    
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        # Распределение цен
                        if 'price' in df.columns:
                            st.subheader("💰 Распределение цен")
                            fig, ax = plt.subplots(figsize=(10, 6))
                            
                            # Фильтруем выбросы
                            price_data = df['price']
                            Q1 = price_data.quantile(0.25)
                            Q3 = price_data.quantile(0.75)
                            IQR = Q3 - Q1
                            filtered_prices = price_data[(price_data >= Q1 - 1.5*IQR) & (price_data <= Q3 + 1.5*IQR)]
                            
                            if not filtered_prices.empty:
                                sns.histplot(filtered_prices, bins=30, kde=True, ax=ax, color='#FF6B35')
                                ax.set_xlabel("Цена, руб")
                                ax.set_ylabel("Количество")
                                ax.grid(True, alpha=0.3)
                                ax.set_title("Распределение цен на автозапчасти")
                                st.pyplot(fig)
                    
                    with col2:
                        # Распределение по категориям
                        if 'auto_category' in df.columns:
                            st.subheader("📁 Распределение по категориям")
                            fig, ax = plt.subplots(figsize=(10, 6))
                            
                            category_counts = df['auto_category'].value_counts().head(10)
                            if not category_counts.empty:
                                sns.barplot(x=category_counts.values, y=category_counts.index, 
                                          ax=ax, palette='viridis')
                                ax.set_xlabel("Количество запчастей")
                                ax.set_title("Топ категорий автозапчастей")
                                plt.tight_layout()
                                st.pyplot(fig)
                    
                    # Анализ брендов
                    if st.session_state.analysis_results:
                        st.subheader("🏆 Анализ брендов")
                        
                        with st.expander("Детальная статистика брендов", expanded=False):
                            for brand, stats in st.session_state.analysis_results.get('brand_price_stats', {}).items():
                                cols = st.columns(4)
                                with cols[0]:
                                    st.metric("Бренд", brand)
                                with cols[1]:
                                    st.metric("Количество", stats['count'])
                                with cols[2]:
                                    st.metric("Средняя цена", f"{stats['avg_price']:,.0f} ₽")
                                with cols[3]:
                                    price_range = f"{stats['min_price']:,.0f}-{stats['max_price']:,.0f}"
                                    st.metric("Диапазон", price_range)
        
        with tab3:
            st.header("🚗 Детальная информация")
            
            if not df.empty:
                # Фильтры
                st.subheader("🔍 Фильтрация")
                
                filter_cols = st.columns(4)
                
                with filter_cols[0]:
                    if 'brand' in df.columns:
                        brands = ['Все'] + sorted(df['brand'].dropna().unique().tolist())
                        selected_brand = st.selectbox("Бренд", brands, key="detail_brand")
                
                with filter_cols[1]:
                    if 'auto_category' in df.columns:
                        categories = ['Все'] + sorted(df['auto_category'].dropna().unique().tolist())
                        selected_category = st.selectbox("Категория", categories, key="detail_category")
                
                with filter_cols[2]:
                    if 'price' in df.columns:
                        min_price = float(df['price'].min())
                        max_price = float(df['price'].max())
                        price_range = st.slider("Цена", min_price, max_price, (min_price, max_price), key="detail_price")
                
                with filter_cols[3]:
                    if 'rating' in df.columns:
                        min_rating = float(df['rating'].min())
                        max_rating = float(df['rating'].max())
                        rating_filter = st.slider("Рейтинг", min_rating, max_rating, (min_rating, max_rating), key="detail_rating")
                
                # Применяем фильтры
                filtered_df = df.copy()
                
                if selected_brand != 'Все':
                    filtered_df = filtered_df[filtered_df['brand'] == selected_brand]
                
                if selected_category != 'Все':
                    filtered_df = filtered_df[filtered_df['auto_category'] == selected_category]
                
                filtered_df = filtered_df[
                    (filtered_df['price'] >= price_range[0]) & 
                    (filtered_df['price'] <= price_range[1])
                ]
                
                if 'rating' in filtered_df.columns:
                    filtered_df = filtered_df[
                        (filtered_df['rating'] >= rating_filter[0]) & 
                        (filtered_df['rating'] <= rating_filter[1])
                    ]
                
                st.info(f"Найдено запчастей: {len(filtered_df)}")
                
                # Выбор запчасти для детального просмотра
                if not filtered_df.empty:
                    st.subheader("🔧 Выберите запчасть")
                    
                    # Создаем список для выбора
                    part_options = []
                    for idx, row in filtered_df.iterrows():
                        name = row.get('name', f'Запчасть {idx}')
                        brand = row.get('brand', '')
                        price = row.get('price', 0)
                        
                        display_name = f"{brand} - {name[:50]}... - {price:,.0f} ₽" if len(name) > 50 else f"{brand} - {name} - {price:,.0f} ₽"
                        part_options.append((idx, display_name))
                    
                    selected_option = st.selectbox(
                        "Запчасть",
                        options=range(len(part_options)),
                        format_func=lambda x: part_options[x][1],
                        key="part_selector"
                    )
                    
                    if selected_option is not None:
                        part_idx = part_options[selected_option][0]
                        part = filtered_df.iloc[part_idx]
                        
                        # Отображаем детальную информацию
                        st.markdown("---")
                        
                        col1, col2 = st.columns([2, 1])
                        
                        with col1:
                            st.subheader("📋 Основная информация")
                            
                            # Основные поля
                            basic_fields = ['name', 'brand', 'price', 'sale_price', 'sale', 
                                         'rating', 'feedbacks', 'supplier', 'auto_category']
                            
                            for field in basic_fields:
                                if field in part and pd.notna(part[field]):
                                    if field == 'price' or field == 'sale_price':
                                        st.write(f"**{field.replace('_', ' ').title()}:** {part[field]:,.2f} ₽")
                                    elif field == 'sale':
                                        st.write(f"**Скидка:** {part[field]}%")
                                    elif field == 'rating':
                                        st.write(f"**Рейтинг:** {part[field]:.2f}/5")
                                    elif field == 'feedbacks':
                                        st.write(f"**Отзывов:** {int(part[field])}")
                                    else:
                                        st.write(f"**{field.replace('_', ' ').title()}:** {part[field]}")
                            
                            # Ссылка
                            if 'url' in part and part['url']:
                                st.markdown(f"[🔗 Открыть на Wildberries]({part['url']})")
                        
                        with col2:
                            # Изображения
                            if 'pics' in part and pd.notna(part['pics']) and part['pics'] > 0:
                                st.subheader("🖼️ Изображения")
                                st.info(f"Доступно {int(part['pics'])} изображений")
                            
                            # Характеристики
                            st.subheader("⚙️ Ключевые характеристики")
                            
                            # Автомобильная информация
                            auto_fields = ['car_brand', 'car_model', 'production_year', 'engine', 
                                        'engine_capacity', 'fuel_type', 'compatible_brands']
                            
                            for field in auto_fields:
                                if field in part and pd.notna(part[field]) and part[field]:
                                    st.write(f"**{field.replace('_', ' ').title()}:** {part[field]}")
                        
                        # Детальные характеристики
                        st.subheader("🔧 Технические характеристики")
                        
                        # Находим все характеристики
                        exclude_fields = ['id', 'wb_id', 'product_id', 'name', 'brand', 'price', 'sale_price',
                                        'rating', 'feedbacks', 'url', 'pics', 'supplier', 'parse_status',
                                        'error_message', 'parse_timestamp', 'characteristics_raw',
                                        'page_characteristics', 'auto_category']
                        
                        tech_fields = []
                        for col in df.columns:
                            if col not in exclude_fields and col in part and pd.notna(part[col]) and part[col]:
                                tech_fields.append(col)
                        
                        if tech_fields:
                            # Группируем характеристики
                            groups = {
                                'Общие': ['article', 'vendor_code', 'oem_number', 'country', 'manufacturer'],
                                'Применяемость': ['compatible_brands', 'compatible_models', 'compatible_years',
                                                 'production_year', 'production_years', 'vin_code'],
                                'Технические': ['material', 'size', 'weight', 'length', 'width', 'height',
                                              'diameter', 'thickness'],
                                'Двигатель': ['engine', 'engine_capacity', 'engine_power', 'fuel_type'],
                                'Трансмиссия': ['transmission_type', 'drive_type'],
                                'Упаковка': ['quantity_per_pack', 'unit', 'packaging'],
                                'Гарантия': ['warranty', 'lifespan', 'expiry_date']
                            }
                            
                            for group_name, field_list in groups.items():
                                group_fields = [f for f in field_list if f in tech_fields]
                                if group_fields:
                                    with st.expander(f"📁 {group_name}", expanded=False):
                                        for field in group_fields:
                                            st.write(f"**{field.replace('_', ' ').title()}:** {part[field]}")
                        else:
                            st.info("Технические характеристики не найдены")
        
        with tab4:
            st.header("💾 Экспорт данных")
            
            if not df.empty:
                # Статистика
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    st.metric("Запчастей", len(df))
                with col2:
                    st.metric("Параметров", len(df.columns))
                with col3:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                
                # Настройки экспорта
                st.subheader("⚙️ Настройки экспорта")
                
                filename = st.text_input(
                    "Имя файла",
                    value=f"auto_parts_{timestamp}"
                )
                
                # Кнопки экспорта
                st.subheader("📥 Скачать данные")
                
                export_cols = st.columns(3)
                
                with export_cols[0]:
                    # Excel
                    if st.button("📊 Excel", use_container_width=True):
                        output = BytesIO()
                        
                        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                            # Основные данные
                            df.to_excel(writer, sheet_name='Автозапчасти', index=False)
                            
                            # Статистика
                            if include_charts:
                                stats_sheet = writer.book.add_worksheet('Статистика')
                                
                                # Основная статистика
                                stats_data = [
                                    ['Всего запчастей', len(df)],
                                    ['Уникальных брендов', df['brand'].nunique() if 'brand' in df.columns else 0],
                                    ['Средняя цена', f"{df['price'].mean():.2f} ₽" if 'price' in df.columns else 'N/A'],
                                    ['Общий диапазон цен', f"{df['price'].min():.2f} - {df['price'].max():.2f} ₽" if 'price' in df.columns else 'N/A'],
                                    ['Дата сбора', datetime.now().strftime("%d.%m.%Y %H:%M")],
                                ]
                                
                                for i, (param, value) in enumerate(stats_data):
                                    stats_sheet.write(i, 0, param)
                                    stats_sheet.write(i, 1, value)
                            
                            # Автоподбор ширины
                            worksheet = writer.sheets['Автозапчасти']
                            for i, col in enumerate(df.columns):
                                column_width = max(df[col].astype(str).map(len).max(), len(col)) + 2
                                worksheet.set_column(i, i, min(column_width, 50))
                        
                        output.seek(0)
                        
                        st.download_button(
                            label="Скачать Excel",
                            data=output,
                            file_name=f"{filename}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True
                        )
                
                with export_cols[1]:
                    # CSV
                    csv_data = df.to_csv(index=False, encoding='utf-8-sig')
                    st.download_button(
                        label="📄 CSV (UTF-8)",
                        data=csv_data.encode('utf-8-sig'),
                        file_name=f"{filename}.csv",
                        mime="text/csv",
                        use_container_width=True
                    )
                
                with export_cols[2]:
                    # JSON
                    json_data = df.to_json(orient='records', force_ascii=False, indent=2)
                    st.download_button(
                        label="🔤 JSON",
                        data=json_data.encode('utf-8'),
                        file_name=f"{filename}.json",
                        mime="application/json",
                        use_container_width=True
                    )
                
                # Предпросмотр
                st.subheader("👁️ Предпросмотр данных")
                
                preview_rows = st.slider("Строк для предпросмотра", 5, 50, 10)
                
                # Основные колонки для предпросмотра
                preview_cols = ['name', 'brand', 'price', 'auto_category', 'rating', 'feedbacks']
                available_cols = [col for col in preview_cols if col in df.columns]
                
                if available_cols:
                    preview_df = df[available_cols].head(preview_rows)
                    
                    # Форматирование
                    if 'price' in preview_df.columns:
                        preview_df['price'] = preview_df['price'].apply(lambda x: f"{x:,.0f} ₽")
                    
                    st.dataframe(
                        preview_df,
                        use_container_width=True,
                        height=400
                    )
    
    else:
        # Инструкция
        with tab1:
            st.info("""
            ## 🚗 Парсинг автозапчастей Wildberries
            
            ### 📋 Возможности:
            
            **🔍 4 режима поиска:**
            1. **По запросу** - поиск по названию/описанию
            2. **По категории** - поиск в конкретной категории запчастей
            3. **По артикулам** - поиск конкретных товаров по артикулам WB
            4. **Анализ брендов** - глубокий анализ конкретного бренда
            
            **📊 Собираемые данные:**
            - Основная информация (название, бренд, цена, рейтинг)
            - Технические характеристики
            - Совместимость с автомобилями (марка, модель, год)
            - Информация о продавце
            - Категория запчасти (автоопределение)
            
            **⚙️ Специализированные функции:**
            - Автоматическое определение категории запчасти
            - Извлечение информации о совместимости
            - Нормализация технических характеристик
            - Анализ брендов и ценовых диапазонов
            
            ### 🎯 Рекомендации по поиску:
            
            **Для лучших результатов используйте:**
            - Конкретные названия запчастей ("тормозные колодки")
            - Номера оригинальных запчастей ("OEM 123456")
            - Названия брендов ("Bosch свечи зажигания")
            - Модели автомобилей ("Audi A4 фильтр")
            
            **Примеры запросов:**
            - `тормозные колодки`
            - `масляный фильтр Toyota`
            - `свечи зажигания NGK`
            - `амортизаторы Bilstein`
            - `аккумулятор Bosch 60Ач`
            
            ### ⚡ Настройки:
            
            **Скорость парсинга:**
            - **Медленный:** 2-4 сек между запросами (максимальная безопасность)
            - **Сбалансированный:** 1.2-2.5 сек (рекомендуется)
            - **Быстрый:** 0.8-1.5 сек (риск блокировки)
            
            **Детальный парсинг:**
            - Включает сбор всех характеристик из карточки товара
            - Определение категории и совместимости
            - Занимает больше времени, но дает полную информацию
            
            ### 🚀 Начните работу:
            1. Выберите режим поиска в боковой панели
            2. Введите параметры поиска
            3. Настройте скорость парсинга
            4. Нажмите кнопку "Начать парсинг"
            """)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        st.error(f"❌ Ошибка запуска приложения: {e}")
        st.info("""
        ### 📦 Установка зависимостей:
        
        ```bash
        # Основные зависимости
        pip install streamlit pandas requests matplotlib seaborn xlsxwriter
        
        # Дополнительные зависимости
        pip install fake-useragent PyYAML
        
        # Запуск приложения
        streamlit run auto_parts_parser.py
        ```
        
        ### 🔧 Если возникают проблемы:
        
        1. **Обновите зависимости:**
        ```bash
        pip install --upgrade streamlit pandas requests
        ```
        
        2. **Проверьте версию Python (рекомендуется 3.8+):**
        ```bash
        python --version
        ```
        
        3. **При ошибках с fake-useragent:**
        ```bash
        pip install --upgrade fake-useragent
        ```
        """)
