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
from datetime import datetime
import hashlib
import urllib.parse
from typing import List, Dict, Optional, Tuple, Any, Set
import logging
from fake_useragent import UserAgent
from concurrent.futures import ThreadPoolExecutor, as_completed
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import cloudscraper
from fp.fp import FreeProxy
import asyncio
import aiohttp
import yaml
import sqlite3
from pathlib import Path
import pickle
import brotli
import zlib

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== КОНФИГУРАЦИЯ ====================

class Config:
    """Конфигурация парсера"""
    
    def __init__(self, config_file: str = "config.yaml"):
        self.config_file = config_file
        self.default_config = {
            'parsing': {
                'delay_min': 1.0,
                'delay_max': 2.0,
                'max_workers': 5,
                'use_proxies': True,
                'use_selenium': False,
                'max_retries': 3,
                'timeout': 30,
            },
            'export': {
                'default_format': 'excel',
                'include_stats': True,
                'include_charts': True,
                'split_by_category': False,
            },
            'api': {
                'cache_ttl': 3600,
                'enable_cache': True,
                'compression': True,
            },
            'selenium': {
                'headless': True,
                'window_width': 1920,
                'window_height': 1080,
            }
        }
        
        self.config = self.load_config()
    
    def load_config(self) -> Dict:
        """Загрузка конфигурации из файла"""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                # Объединяем с дефолтными значениями
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
    
    def save_config(self):
        """Сохранение конфигурации в файл"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                yaml.dump(self.config, f, default_flow_style=False, allow_unicode=True)
        except Exception as e:
            logger.error(f"Ошибка сохранения конфига: {e}")

# ==================== КЕШИРОВАНИЕ ====================

class CacheManager:
    """Менеджер кэширования"""
    
    def __init__(self, cache_dir: str = "cache", ttl: int = 3600):
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
        # Сначала проверяем в памяти
        if key in self.memory_cache:
            cached_data = self.memory_cache[key]
            if time.time() - cached_data['timestamp'] < self.ttl:
                return cached_data['data']
        
        # Затем в файловом кэше
        cache_file = self.cache_dir / f"{key}.pkl"
        if cache_file.exists():
            try:
                with open(cache_file, 'rb') as f:
                    cached_data = pickle.load(f)
                if time.time() - cached_data['timestamp'] < self.ttl:
                    # Обновляем кэш в памяти
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
        
        # Сохраняем в памяти
        self.memory_cache[key] = cached_data
        
        # Сохраняем в файл
        cache_file = self.cache_dir / f"{key}.pkl"
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(cached_data, f)
        except Exception as e:
            logger.error(f"Ошибка сохранения кэша: {e}")

# ==================== КЛАСС ДЛЯ ОБХОДА ЗАЩИТЫ ====================

class ProxyManager:
    """Продвинутый менеджер прокси"""
    
    def __init__(self, config: Dict):
        self.proxies = []
        self.bad_proxies = set()
        self.proxy_stats = {}
        self.config = config
        self.last_update = datetime.now()
        self.proxy_sources = [
            self._get_free_proxy,
            self._get_spys_one_proxies,
            self._get_proxy_list_proxies,
        ]
        
    def _get_free_proxy(self) -> List[str]:
        """Получение прокси из FreeProxy"""
        try:
            proxy = FreeProxy(rand=True, timeout=2).get()
            if proxy:
                return [proxy]
        except:
            pass
        return []
    
    def _get_spys_one_proxies(self) -> List[str]:
        """Получение прокси со Spys.one"""
        try:
            response = requests.get('https://spys.one/proxies/', timeout=5)
            # Парсинг HTML для получения прокси
            # Упрощенная версия
            return []
        except:
            return []
    
    def _get_proxy_list_proxies(self) -> List[str]:
        """Получение прокси с Proxy-List"""
        try:
            response = requests.get('https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all', timeout=5)
            if response.status_code == 200:
                proxies = response.text.strip().split('\n')
                return [p.strip() for p in proxies if p.strip()]
        except:
            pass
        return []
    
    def update_proxies(self):
        """Обновление списка прокси из всех источников"""
        new_proxies = []
        
        for source_func in self.proxy_sources:
            try:
                proxies = source_func()
                new_proxies.extend(proxies)
                if proxies:
                    logger.info(f"Получено {len(proxies)} прокси из {source_func.__name__}")
            except Exception as e:
                logger.error(f"Ошибка получения прокси: {e}")
        
        # Очищаем плохие прокси
        self.proxies = [p for p in set(new_proxies) if p not in self.bad_proxies]
        self.last_update = datetime.now()
        logger.info(f"Обновлены прокси. Всего доступно: {len(self.proxies)}")
    
    def get_proxy(self) -> Optional[Dict]:
        """Получение лучшего прокси на основе статистики"""
        if not self.proxies or (datetime.now() - self.last_update).seconds > 600:
            self.update_proxies()
        
        if not self.proxies:
            return None
        
        # Выбираем прокси с лучшей статистикой
        available = [p for p in self.proxies if p not in self.bad_proxies]
        if not available:
            return None
        
        proxy = random.choice(available)
        return {
            'http': f'http://{proxy}',
            'https': f'http://{proxy}'
        }
    
    def mark_bad(self, proxy: str):
        """Пометить прокси как плохой"""
        self.bad_proxies.add(proxy)
        logger.warning(f"Прокси помечен как плохой: {proxy}")

class RequestManager:
    """Продвинутое управление запросами"""
    
    def __init__(self, config: Dict):
        self.ua = UserAgent()
        self.proxy_manager = ProxyManager(config) if config['parsing']['use_proxies'] else None
        self.session = requests.Session()
        self.session.headers.update(self.get_base_headers())
        self.delay_range = (config['parsing']['delay_min'], config['parsing']['delay_max'])
        self.max_retries = config['parsing']['max_retries']
        self.request_stats = {
            'total': 0,
            'success': 0,
            'failed': 0,
            'avg_time': 0,
        }
        self.response_times = []
        self.cache_manager = CacheManager() if config['api']['enable_cache'] else None
        
    def get_base_headers(self) -> Dict:
        """Базовые заголовки"""
        return {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Connection': 'keep-alive',
            'DNT': '1',
            'Origin': 'https://www.wildberries.ru',
            'Referer': 'https://www.wildberries.ru/',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'cross-site',
            'User-Agent': self.ua.random,
            'sec-ch-ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }
    
    def get_headers(self, additional: Dict = None) -> Dict:
        """Генерация заголовков с рандомизацией"""
        headers = self.get_base_headers()
        headers['User-Agent'] = self.ua.random
        
        # Рандомизация некоторых параметров
        if random.random() > 0.5:
            headers['Accept-Encoding'] = random.choice(['gzip, deflate, br', 'gzip', 'deflate', 'br'])
        
        if additional:
            headers.update(additional)
        
        return headers
    
    def make_request(self, url: str, method: str = 'GET', use_cache: bool = True, **kwargs) -> Optional[requests.Response]:
        """Выполнение запроса с продвинутой логикой"""
        
        # Проверяем кэш
        cache_key = None
        if use_cache and self.cache_manager and method == 'GET':
            params = kwargs.get('params', {})
            cache_key = self.cache_manager.get_key(url, params)
            cached_data = self.cache_manager.get(cache_key)
            if cached_data:
                logger.debug(f"Используем кэш для {url}")
                return self._create_response_from_cache(cached_data)
        
        # Случайная задержка
        delay = random.uniform(*self.delay_range)
        time.sleep(delay)
        
        # Подготовка параметров
        proxy = None
        if self.proxy_manager:
            proxy = self.proxy_manager.get_proxy()
        
        headers = self.get_headers(kwargs.pop('headers', {}))
        
        # Ротация параметров запроса
        if 'params' in kwargs:
            kwargs['params'] = self.rotate_params(kwargs['params'])
        
        # Выполнение запроса с повторными попытками
        start_time = time.time()
        
        for attempt in range(self.max_retries):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    proxies=proxy,
                    timeout=30,
                    **kwargs
                )
                
                response_time = time.time() - start_time
                self.response_times.append(response_time)
                self.request_stats['avg_time'] = sum(self.response_times) / len(self.response_times)
                
                self.request_stats['total'] += 1
                
                if response.status_code == 200:
                    self.request_stats['success'] += 1
                    
                    # Сохраняем в кэш
                    if cache_key and self.cache_manager:
                        self.cache_manager.set(cache_key, {
                            'status_code': response.status_code,
                            'headers': dict(response.headers),
                            'content': response.content,
                            'encoding': response.encoding,
                        })
                    
                    return response
                else:
                    self.request_stats['failed'] += 1
                    logger.warning(f"Статус {response.status_code} для {url}")
                    
                    if response.status_code in [429, 403]:  # Слишком много запросов или запрещено
                        if proxy:
                            self.proxy_manager.mark_bad(proxy['http'].replace('http://', ''))
                        time.sleep(random.uniform(5, 10))
                    
            except Exception as e:
                self.request_stats['failed'] += 1
                logger.error(f"Ошибка запроса (попытка {attempt + 1}): {e}")
                
                if proxy:
                    self.proxy_manager.mark_bad(proxy['http'].replace('http://', ''))
                
                if attempt < self.max_retries - 1:
                    wait_time = random.uniform(2, 5) * (attempt + 1)
                    time.sleep(wait_time)
        
        return None
    
    def _create_response_from_cache(self, cached_data: Dict) -> requests.Response:
        """Создание объекта Response из кэша"""
        response = requests.Response()
        response.status_code = cached_data['status_code']
        response.headers = cached_data['headers']
        response._content = cached_data['content']
        response.encoding = cached_data['encoding']
        return response
    
    def rotate_params(self, params: Dict) -> Dict:
        """Ротация параметров запроса"""
        rotated = params.copy()
        
        # Добавляем случайные параметры
        if random.random() > 0.7:
            rotated['_'] = str(int(time.time() * 1000))
        
        if random.random() > 0.8:
            rotated['rand'] = random.randint(1000, 9999)
        
        return rotated
    
    def get_stats(self) -> Dict:
        """Получение статистики запросов"""
        stats = self.request_stats.copy()
        stats['cache_hits'] = self.cache_manager.hits if self.cache_manager else 0
        stats['cache_misses'] = self.cache_manager.misses if self.cache_manager else 0
        return stats

# ==================== УЛУЧШЕННЫЙ ПАРСЕР ХАРАКТЕРИСТИК ====================

class CharacteristicNormalizer:
    """Нормализация характеристик товаров"""
    
    # Словарь для нормализации названий характеристик
    CHAR_NORMALIZATION = {
        # Бренд и производитель
        'Бренд': 'brand',
        'Производитель': 'manufacturer',
        'Торговая марка': 'trademark',
        
        # Основные характеристики
        'Артикул': 'article',
        'Артикул производителя': 'vendor_code',
        'Штрихкод': 'barcode',
        'Код товара': 'product_code',
        
        # Цвет и внешний вид
        'Цвет': 'color',
        'Цвет товара': 'color',
        'Основной цвет': 'primary_color',
        'Цвет на фото': 'photo_color',
        
        # Размеры и габариты
        'Размер': 'size',
        'Размер товара': 'size',
        'Размеры': 'dimensions',
        'Габариты': 'dimensions',
        'Вес': 'weight',
        'Вес товара': 'weight',
        'Длина': 'length',
        'Ширина': 'width',
        'Высота': 'height',
        'Глубина': 'depth',
        
        # Материалы
        'Материал': 'material',
        'Состав': 'composition',
        'Основной материал': 'main_material',
        'Материал верха': 'upper_material',
        'Материал подошвы': 'sole_material',
        
        # Страна
        'Страна производства': 'country',
        'Страна-изготовитель': 'country',
        'Страна': 'country',
        'Производство': 'manufacture_country',
        
        # Упаковка
        'Упаковка': 'packaging',
        'Вид упаковки': 'packaging_type',
        'Количество в упаковке': 'items_per_pack',
        
        # Дополнительно
        'Серия': 'series',
        'Модель': 'model',
        'Коллекция': 'collection',
        'Сезон': 'season',
        'Пол': 'gender',
        'Возраст': 'age',
        'Рост': 'height_person',
    }
    
    @staticmethod
    def normalize_key(key: str) -> str:
        """Нормализация ключа характеристики"""
        if not key:
            return ''
        
        # Приводим к нижнему регистру и убираем лишние символы
        key = key.strip().lower()
        key = re.sub(r'[^\w\s]', '', key)
        
        # Ищем в словаре нормализации
        for ru_key, en_key in CharacteristicNormalizer.CHAR_NORMALIZATION.items():
            if ru_key.lower() in key:
                return en_key
        
        # Если не нашли, возвращаем оригинальный ключ
        return key
    
    @staticmethod
    def normalize_value(value: str) -> Any:
        """Нормализация значения характеристики"""
        if not value:
            return ''
        
        value = str(value).strip()
        
        # Пытаемся определить тип данных
        if re.match(r'^\d+$', value):
            return int(value)
        elif re.match(r'^\d+\.\d+$', value):
            return float(value)
        elif value.lower() in ['да', 'есть', 'имеется', 'true', 'yes']:
            return True
        elif value.lower() in ['нет', 'отсутствует', 'false', 'no']:
            return False
        
        return value
    
    @staticmethod
    def extract_dimensions(text: str) -> Optional[Dict]:
        """Извлечение размеров из текста"""
        patterns = [
            r'(\d+[.,]?\d*)\s*[×xX*]\s*(\d+[.,]?\d*)\s*[×xX*]\s*(\d+[.,]?\d*)\s*(см|мм|м)',
            r'(\d+[.,]?\d*)\s*[×xX*]\s*(\d+[.,]?\d*)\s*(см|мм|м)',
            r'(\d+)\s*на\s*(\d+)\s*на\s*(\d+)\s*(см|мм|м)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                dimensions = []
                for i in range(1, 4):
                    if i <= len(match.groups()) - 1:  # Последняя группа - единица измерения
                        dim = match.group(i).replace(',', '.')
                        dimensions.append(float(dim))
                
                unit = match.group(-1)
                if unit == 'м':
                    dimensions = [d * 100 for d in dimensions]  # конвертируем в см
                elif unit == 'мм':
                    dimensions = [d / 10 for d in dimensions]  # конвертируем в см
                
                return {
                    'width': dimensions[0] if len(dimensions) > 0 else None,
                    'height': dimensions[1] if len(dimensions) > 1 else None,
                    'depth': dimensions[2] if len(dimensions) > 2 else None,
                    'unit': 'cm'
                }
        
        return None

class EnhancedCardParser:
    """Продвинутый парсер характеристик карточек товаров"""
    
    def __init__(self, request_manager: RequestManager, config: Dict):
        self.request_manager = request_manager
        self.config = config
        self.normalizer = CharacteristicNormalizer()
        self.ua = UserAgent()
        
    def get_all_card_characteristics(self, product_id: str) -> Dict[str, Any]:
        """Получение ВСЕХ характеристик из карточки товара"""
        characteristics_data = {
            'product_id': product_id,
            'parse_timestamp': datetime.now().isoformat(),
            'parse_status': 'success'
        }
        
        try:
            # Основные данные товара
            main_data = self._get_main_product_data(product_id)
            if not main_data:
                characteristics_data['parse_status'] = 'failed_main_data'
                return characteristics_data
            
            characteristics_data.update(main_data)
            
            # Детальные характеристики
            details = self._get_product_details(product_id)
            characteristics_data.update(details)
            
            # Информация о продавце
            seller_info = self._get_seller_info(product_id)
            characteristics_data.update(seller_info)
            
            # Остатки на складах
            stock_info = self._get_stock_info(product_id)
            characteristics_data.update(stock_info)
            
            # Отзывы и рейтинги
            feedback_info = self._get_feedback_info(product_id)
            characteristics_data.update(feedback_info)
            
            # Информация о доставке
            delivery_info = self._get_delivery_info(product_id)
            characteristics_data.update(delivery_info)
            
            # SEO данные
            seo_info = self._get_seo_info(product_id)
            characteristics_data.update(seo_info)
            
            # Нормализуем характеристики
            characteristics_data = self._normalize_characteristics(characteristics_data)
            
            # Извлекаем структурированные данные
            characteristics_data.update(self._extract_structured_data(characteristics_data))
            
        except Exception as e:
            logger.error(f"Ошибка получения характеристик товара {product_id}: {e}")
            characteristics_data.update({
                'parse_status': 'error',
                'error_message': str(e),
                'error_type': type(e).__name__
            })
        
        return characteristics_data
    
    def _get_main_product_data(self, product_id: str) -> Dict:
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
                'brandId': product.get('brandId'),
                'siteBrandId': product.get('siteBrandId'),
                'supplierId': product.get('supplierId'),
                'supplier': product.get('supplier', ''),
                'supplierRating': product.get('supplierRating', 0),
                'priceU': product.get('priceU', 0) / 100,
                'salePriceU': product.get('salePriceU', 0) / 100,
                'basicSale': product.get('basicSale', 0),
                'basicPriceU': product.get('basicPriceU', 0) / 100,
                'promoSale': product.get('promoSale', 0),
                'promoPriceU': product.get('promoPriceU', 0) / 100,
                'logisticsCost': product.get('logisticsCost', 0) / 100,
                'sale': product.get('sale', 0),
                'diffPrice': product.get('diffPrice', False),
                'promoTextCard': product.get('promoTextCard', ''),
                'promoTextCat': product.get('promoTextCat', ''),
                'rating': product.get('rating', 0),
                'feedbacks': product.get('feedbacks', 0),
                'reviewRating': product.get('reviewRating', 0),
                'pics': product.get('pics', 0),
                'volume': product.get('volume', 0),
                'viewFlags': product.get('viewFlags', 0),
                'time1': product.get('time1', 0),
                'time2': product.get('time2', 0),
                'wh': product.get('wh', 0),
                'dtype': product.get('dtype', 0),
                'root': product.get('root', 0),
                'kindId': product.get('kindId', 0),
                'subjectId': product.get('subjectId', 0),
                'subjectParentId': product.get('subjectParentId', 0),
            }
            
            # Цвета
            colors = []
            color_ids = []
            for color in product.get('colors', []):
                colors.append(color.get('name', ''))
                color_ids.append(color.get('id'))
            
            result.update({
                'colors': '; '.join(colors),
                'color_ids': '; '.join(map(str, color_ids)),
            })
            
            # Размеры
            sizes = []
            size_ids = []
            for size_data in product.get('sizes', []):
                size_name = size_data.get('name', '')
                if size_name:
                    sizes.append(size_name)
                    size_ids.append(size_data.get('origName', size_name))
            
            result.update({
                'sizes': '; '.join(sizes),
                'size_ids': '; '.join(size_ids),
            })
            
            # Характеристики из options
            characteristics = {}
            for opt in product.get('options', []):
                name = opt.get('name', '').strip()
                value = opt.get('value', '').strip()
                if name and value:
                    characteristics[name] = value
            
            # Добавляем как отдельное поле
            result['all_characteristics_raw'] = json.dumps(characteristics, ensure_ascii=False)
            
            # И как отдельные колонки
            for key, value in characteristics.items():
                norm_key = self.normalizer.normalize_key(key)
                result[norm_key] = value
            
            # Категории
            categories = product.get('categoryTree', [])
            if categories:
                result['category_full'] = ' > '.join([cat.get('name', '') for cat in categories])
                result['category_ids'] = ' > '.join([str(cat.get('id', '')) for cat in categories])
                result['category_last_id'] = categories[-1].get('id') if categories else ''
            
            return result
            
        except Exception as e:
            logger.error(f"Ошибка получения основных данных: {e}")
            return {}
    
    def _get_product_details(self, product_id: str) -> Dict:
        """Получение детальной информации о товаре"""
        result = {}
        
        try:
            # Определяем корзину
            basket_num = self._get_basket_number(product_id)
            
            # Формируем URL для деталей
            vol = product_id[:4]
            part = str(int(product_id) // 100000)
            
            urls = [
                f"https://basket-{basket_num}.wb.ru/vol{vol}/part{part}/{product_id}/info/ru/card.json",
                f"https://basket-{basket_num}.wb.ru/vol{vol}/part{part}/{product_id}/info/ru/card.json",
            ]
            
            for url in urls:
                response = self.request_manager.make_request(url)
                if response and response.status_code == 200:
                    data = response.json()
                    
                    # Основное описание
                    result.update({
                        'description': data.get('description', ''),
                        'composition': data.get('compositions', ''),
                        'purpose': data.get('purpose', ''),
                        'specification': data.get('specification', ''),
                        'video': data.get('video', {}).get('url', ''),
                    })
                    
                    # Сертификаты
                    certificates = data.get('certificates', [])
                    if certificates:
                        result['certificates'] = json.dumps(certificates, ensure_ascii=False)
                    
                    # Гарантия
                    guarantee = data.get('guarantee', {})
                    if guarantee:
                        result.update({
                            'guarantee_text': guarantee.get('text', ''),
                            'guarantee_period': guarantee.get('period', ''),
                        })
                    
                    # Особенности
                    features = data.get('features', [])
                    if features:
                        result['features'] = '; '.join(features)
                    
                    break
        except Exception as e:
            logger.error(f"Ошибка получения деталей: {e}")
        
        return result
    
    def _get_seller_info(self, product_id: str) -> Dict:
        """Получение информации о продавце"""
        result = {}
        
        try:
            urls = [
                f"https://feedbacks1.wb.ru/feedbacks/v1/{product_id}",
                f"https://feedbacks2.wb.ru/feedbacks/v1/{product_id}",
            ]
            
            for url in urls:
                response = self.request_manager.make_request(url)
                if response and response.status_code == 200:
                    data = response.json()
                    
                    result.update({
                        'seller_feedbacks_count': data.get('feedbackCount', 0),
                        'seller_valuation': data.get('valuation', ''),
                        'seller_site': data.get('site', ''),
                        'seller_inn': data.get('inn', ''),
                        'seller_ogrn': data.get('ogrn', ''),
                        'seller_name': data.get('name', ''),
                        'seller_address': data.get('address', ''),
                    })
                    
                    # Последние отзывы
                    feedbacks = data.get('feedbacks', [])[:5]
                    if feedbacks:
                        result['recent_feedbacks'] = json.dumps(feedbacks, ensure_ascii=False)
                    
                    break
        except Exception as e:
            logger.error(f"Ошибка получения информации о продавце: {e}")
        
        return result
    
    def _get_stock_info(self, product_id: str) -> Dict:
        """Получение информации об остатках"""
        result = {}
        
        try:
            url = f"https://product-order-qnt.wildberries.ru/by-nm/?nm={product_id}"
            response = self.request_manager.make_request(url)
            
            if response and response.status_code == 200:
                stock_data = response.json()
                
                if isinstance(stock_data, list):
                    total_stock = sum(item.get('qnt', 0) for item in stock_data)
                    warehouses = []
                    
                    for item in stock_data:
                        warehouses.append({
                            'warehouse_id': item.get('warehouseId'),
                            'warehouse_name': item.get('warehouseName'),
                            'quantity': item.get('qnt', 0),
                        })
                    
                    result.update({
                        'stock_total': total_stock,
                        'warehouses_count': len(stock_data),
                        'warehouses_info': json.dumps(warehouses, ensure_ascii=False),
                    })
        except Exception as e:
            logger.error(f"Ошибка получения информации об остатках: {e}")
        
        return result
    
    def _get_feedback_info(self, product_id: str) -> Dict:
        """Получение информации об отзывах"""
        result = {}
        
        try:
            url = f"https://feedbacks2.wb.ru/feedbacks/v1/{product_id}"
            response = self.request_manager.make_request(url)
            
            if response and response.status_code == 200:
                data = response.json()
                
                # Статистика отзывов
                feedbacks_summary = data.get('feedbacksSummary', {})
                result.update({
                    'feedbacks_total': data.get('feedbackCount', 0),
                    'feedbacks_with_photo': feedbacks_summary.get('photo', 0),
                    'feedbacks_with_video': feedbacks_summary.get('video', 0),
                    'feedbacks_1_star': feedbacks_summary.get('countOne', 0),
                    'feedbacks_2_star': feedbacks_summary.get('countTwo', 0),
                    'feedbacks_3_star': feedbacks_summary.get('countThree', 0),
                    'feedbacks_4_star': feedbacks_summary.get('countFour', 0),
                    'feedbacks_5_star': feedbacks_summary.get('countFive', 0),
                })
                
                # Детальные отзывы
                detailed_feedbacks = data.get('feedbacks', [])[:10]
                if detailed_feedbacks:
                    simplified_feedbacks = []
                    for fb in detailed_feedbacks:
                        simplified_feedbacks.append({
                            'id': fb.get('id'),
                            'text': fb.get('text', '')[:200],
                            'rating': fb.get('productValuation', 0),
                            'created_date': fb.get('createdDate', ''),
                            'has_photo': bool(fb.get('photoLinks')),
                            'has_video': bool(fb.get('videoLinks')),
                        })
                    result['detailed_feedbacks'] = json.dumps(simplified_feedbacks, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Ошибка получения информации об отзывах: {e}")
        
        return result
    
    def _get_delivery_info(self, product_id: str) -> Dict:
        """Получение информации о доставке"""
        result = {}
        
        try:
            # Информация о доставке обычно есть в основном ответе
            # Для дополнительной информации можно использовать другой эндпоинт
            url = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={product_id}"
            response = self.request_manager.make_request(url)
            
            if response and response.status_code == 200:
                data = response.json()
                product = data.get('data', {}).get('products', [{}])[0]
                
                result.update({
                    'delivery_time': product.get('time1', 0),
                    'delivery_time_extended': product.get('time2', 0),
                    'warehouse_id': product.get('wh', 0),
                })
        except Exception as e:
            logger.error(f"Ошибка получения информации о доставке: {e}")
        
        return result
    
    def _get_seo_info(self, product_id: str) -> Dict:
        """Получение SEO информации"""
        return {
            'url': f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx",
            'url_mobile': f"https://m.wb.ru/catalog/{product_id}/detail.aspx",
            'canonical_url': f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx",
        }
    
    def _get_basket_number(self, product_id: str) -> str:
        """Определение номера корзины"""
        try:
            num = int(product_id[-3:])
            basket_map = [
                (0, 143, '01'), (144, 287, '02'), (288, 431, '03'),
                (432, 719, '04'), (720, 1007, '05'), (1008, 1061, '06'),
                (1062, 1115, '07'), (1116, 1169, '08'), (1170, 1313, '09'),
                (1314, 1601, '10'), (1602, 1655, '11'), (1656, 1919, '12'),
                (1920, 2045, '13'), (2046, 2189, '14'), (2190, 2405, '15'),
                (2406, 2621, '16'), (2622, 2837, '17'), (2838, 3053, '18'),
                (3054, 3269, '19'), (3270, 3485, '20'), (3486, 3701, '21'),
                (3702, 3917, '22'), (3918, 4133, '23'), (4134, 4349, '24'),
                (4350, 4565, '25'), (4566, 4781, '26'), (4782, 4997, '27'),
                (4998, 5213, '28'), (5214, 5429, '29'), (5430, 5645, '30'),
                (5646, 5861, '31'), (5862, 6077, '32'), (6078, 6293, '33'),
                (6294, 6509, '34'), (6510, 6725, '35'), (6726, 6941, '36'),
                (6942, 7157, '37'), (7158, 7373, '38'), (7374, 7589, '39'),
                (7590, 7805, '40'), (7806, 8021, '41'), (8022, 8237, '42'),
                (8238, 8453, '43'), (8454, 8669, '44'), (8670, 8885, '45'),
                (8886, 9101, '46'), (9102, 9317, '47'), (9318, 9533, '48'),
                (9534, 9749, '49'), (9750, 9965, '50'), (9966, 10181, '51'),
                (10182, 10397, '52'), (10398, 10613, '53'), (10614, 10829, '54'),
                (10830, 11045, '55'), (11046, 11261, '56'), (11262, 11477, '57'),
                (11478, 11693, '58'), (11694, 11909, '59'), (11910, 12125, '60'),
                (12126, 12341, '61'), (12342, 12557, '62'), (12558, 12773, '63'),
                (12774, 12989, '64'), (12990, 13205, '65'), (13206, 13421, '66'),
                (13422, 13637, '67'), (13638, 13853, '68'), (13854, 14069, '69'),
                (14070, 14285, '70'), (14286, 14501, '71'), (14502, 14717, '72'),
                (14718, 14933, '73'), (14934, 15149, '74'), (15150, 15365, '75'),
                (15366, 15581, '76'), (15582, 15797, '77'), (15798, 16013, '78'),
                (16014, 16229, '79'), (16230, 16445, '80'), (16446, 16661, '81'),
                (16662, 16877, '82'), (16878, 17093, '83'), (17094, 17309, '84'),
                (17310, 17525, '85'), (17526, 17741, '86'), (17742, 17957, '87'),
                (17958, 18173, '88'), (18174, 18389, '89'), (18390, 18605, '90'),
                (18606, 18821, '91'), (18822, 19037, '92'), (19038, 19253, '93'),
                (19254, 19469, '94'), (19470, 19685, '95'), (19686, 19901, '96'),
                (19902, 20117, '97'), (20118, 20333, '98'), (20334, 20549, '99'),
                (20550, 20765, '100'),
            ]
            
            for start, end, basket in basket_map:
                if start <= num <= end:
                    return basket
        except:
            pass
        return '01'
    
    def _normalize_characteristics(self, data: Dict) -> Dict:
        """Нормализация всех характеристик"""
        normalized = {}
        
        for key, value in data.items():
            if key == 'all_characteristics_raw':
                # Парсим и нормализуем характеристики из JSON
                try:
                    chars = json.loads(value)
                    for char_key, char_value in chars.items():
                        norm_key = self.normalizer.normalize_key(char_key)
                        norm_value = self.normalizer.normalize_value(char_value)
                        normalized[norm_key] = norm_value
                except:
                    normalized[key] = value
            else:
                # Нормализуем ключ и значение
                norm_key = self.normalizer.normalize_key(key)
                norm_value = self.normalizer.normalize_value(value)
                normalized[norm_key] = norm_value
        
        return normalized
    
    def _extract_structured_data(self, data: Dict) -> Dict:
        """Извлечение структурированных данных из характеристик"""
        result = {}
        
        # Извлекаем размеры из текста
        for key in ['dimensions', 'description', 'specification']:
            if key in data:
                dimensions = self.normalizer.extract_dimensions(str(data[key]))
                if dimensions:
                    result.update({
                        'structured_width': dimensions.get('width'),
                        'structured_height': dimensions.get('height'),
                        'structured_depth': dimensions.get('depth'),
                        'structured_unit': dimensions.get('unit'),
                    })
                    break
        
        # Извлекаем цвет из характеристик
        color_keys = ['color', 'primary_color', 'photo_color', 'colors']
        for key in color_keys:
            if key in data and data[key]:
                result['structured_color'] = str(data[key]).split(';')[0].strip()
                break
        
        # Извлекаем материал
        material_keys = ['material', 'main_material', 'composition']
        for key in material_keys:
            if key in data and data[key]:
                result['structured_material'] = str(data[key]).split(';')[0].strip()
                break
        
        return result

# ==================== ОСНОВНОЙ КЛИЕНТ WB ====================

class WBApiClient:
    """Основной клиент для работы с API Wildberries"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.request_manager = RequestManager(config)
        self.cloud_scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'mobile': False
            }
        )
        self.card_parser = EnhancedCardParser(self.request_manager, config)
        
    def search_products(self, query: str, page: int = 1, limit: int = 100, 
                       sort: str = 'popular', category: int = None) -> pd.DataFrame:
        """Универсальный поиск товаров"""
        
        # Пробуем разные методы поиска
        methods = [
            self._search_api_v4,
            self._search_api_v5,
            self._search_catalog_v1,
        ]
        
        for method in methods:
            try:
                result = method(query, page, limit, sort, category)
                if not result.empty:
                    logger.info(f"Успешный поиск через {method.__name__}")
                    return result
            except Exception as e:
                logger.warning(f"Ошибка в методе {method.__name__}: {e}")
                continue
        
        return pd.DataFrame()
    
    def _search_api_v4(self, query: str, page: int, limit: int, 
                      sort: str, category: int = None) -> pd.DataFrame:
        """Поиск через API v4"""
        url = "https://search.wb.ru/exactmatch/ru/common/v4/search"
        
        params = {
            'appType': 1,
            'curr': 'rub',
            'dest': -1257786,
            'lang': 'ru',
            'locale': 'ru',
            'page': page,
            'query': query,
            'resultset': 'catalog',
            'sort': sort,
            'spp': 30,
            'suppressSpellcheck': 'false',
        }
        
        if category:
            params['subject'] = category
        
        if page == 1:
            params['limit'] = min(limit, 300)
        else:
            params['limit'] = min(limit, 300)
        
        response = self.request_manager.make_request(url, params=params)
        if not response or response.status_code != 200:
            return pd.DataFrame()
        
        return self._parse_search_response(response.json())
    
    def _search_api_v5(self, query: str, page: int, limit: int,
                      sort: str, category: int = None) -> pd.DataFrame:
        """Поиск через API v5"""
        url = "https://search.wb.ru/exactmatch/ru/common/v5/search"
        
        params = {
            'appType': 1,
            'curr': 'rub',
            'dest': -1257786,
            'lang': 'ru',
            'locale': 'ru',
            'page': page,
            'query': query,
            'resultset': 'catalog',
            'sort': sort,
            'spp': 30,
            'suppressSpellcheck': 'false',
        }
        
        if category:
            params['subject'] = category
        
        if page == 1:
            params['limit'] = min(limit, 300)
        else:
            params['limit'] = min(limit, 300)
        
        response = self.request_manager.make_request(url, params=params)
        if not response or response.status_code != 200:
            return pd.DataFrame()
        
        return self._parse_search_response(response.json())
    
    def _search_catalog_v1(self, query: str, page: int, limit: int,
                          sort: str, category: int = None) -> pd.DataFrame:
        """Поиск через каталог"""
        # Определяем категорию по умолчанию, если не указана
        if not category:
            category = self._detect_category(query)
        
        url = f"https://catalog.wb.ru/catalog/catalog"
        
        params = {
            'appType': 1,
            'curr': 'rub',
            'dest': -1257786,
            'lang': 'ru',
            'locale': 'ru',
            'page': page,
            'sort': sort,
            'spp': 30,
            'subject': category,
            'search': query,
        }
        
        response = self.request_manager.make_request(url, params=params)
        if not response or response.status_code != 200:
            return pd.DataFrame()
        
        return self._parse_search_response(response.json())
    
    def _detect_category(self, query: str) -> int:
        """Определение категории по запросу"""
        query_lower = query.lower()
        
        # Карта категорий Wildberries (актуально на 2026 год)
        category_map = {
            # Электроника
            ('ноутбук', 'компьютер', 'монитор', 'клавиатура', 'мышь'): 17,
            ('смартфон', 'телефон', 'планшет', 'гаджет', 'умные часы'): 17,
            ('наушники', 'колонки', 'акустика', 'аудио'): 17,
            ('телевизор', 'проектор', 'видео'): 17,
            ('фотоаппарат', 'камера', 'объектив'): 17,
            
            # Одежда и обувь
            ('футболка', 'рубашка', 'блузка', 'топ'): 4,
            ('джинсы', 'брюки', 'штаны', 'шорты'): 4,
            ('платье', 'юбка', 'сарафан', 'туника'): 4,
            ('куртка', 'пальто', 'пуховик', 'ветровка'): 4,
            ('обувь', 'кроссовки', 'туфли', 'ботинки', 'сапоги'): 4,
            
            # Дом и сад
            ('мебель', 'диван', 'кровать', 'стол', 'стул'): 12,
            ('техника', 'холодильник', 'стиральная', 'пылесос', 'микроволновка'): 12,
            ('посуда', 'кухня', 'ножи', 'кастрюля'): 12,
            ('текстиль', 'постельное', 'полотенце', 'ковер'): 12,
            ('ремонт', 'инструмент', 'краска', 'обои'): 12,
            
            # Красота и здоровье
            ('косметика', 'крем', 'шампунь', 'гель', 'мыло'): 16,
            ('парфюм', 'духи', 'туалетная вода'): 16,
            ('уход', 'косметичка', 'кисть', 'спонж'): 16,
            ('здоровье', 'витамины', 'бад', 'аптечка'): 16,
            
            # Детские товары
            ('игрушка', 'конструктор', 'кукла', 'машинка'): 10,
            ('детск', 'ребенок', 'малыш'): 10,
            ('коляска', 'автокресло', 'стульчик'): 10,
            ('питание', 'подгузник', 'памперс'): 10,
            
            # Спорт
            ('спорт', 'тренажер', 'гантел', 'штанга'): 9,
            ('велосипед', 'самокат', 'скейт', 'ролики'): 9,
            ('лыжи', 'сноуборд', 'коньки'): 9,
            ('мяч', 'ракетка', 'снаряд'): 9,
        }
        
        for keywords, category_id in category_map.items():
            for keyword in keywords:
                if keyword in query_lower:
                    return category_id
        
        return 0  # Все товары
    
    def _parse_search_response(self, data: Dict) -> pd.DataFrame:
        """Парсинг ответа поиска"""
        try:
            products = data.get('data', {}).get('products', [])
            if not products:
                return pd.DataFrame()
            
            items = []
            for p in products:
                try:
                    item = self._parse_product_item(p)
                    items.append(item)
                except Exception as e:
                    logger.error(f"Ошибка парсинга товара: {e}")
                    continue
            
            return pd.DataFrame(items)
            
        except Exception as e:
            logger.error(f"Ошибка парсинга ответа поиска: {e}")
            return pd.DataFrame()
    
    def _parse_product_item(self, product: Dict) -> Dict:
        """Парсинг одного товара"""
        # Цвета
        colors = []
        color_ids = []
        for color in product.get('colors', []):
            colors.append(color.get('name', ''))
            color_ids.append(color.get('id'))
        
        # Размеры
        sizes = []
        size_ids = []
        for size_data in product.get('sizes', []):
            size_name = size_data.get('name', '')
            if size_name:
                sizes.append(size_name)
                size_ids.append(size_data.get('origName', size_name))
        
        # Характеристики
        characteristics = {}
        for opt in product.get('options', []):
            name = opt.get('name', '').strip()
            value = opt.get('value', '').strip()
            if name and value:
                characteristics[name] = value
        
        return {
            'id': product.get('id'),
            'root': product.get('root', 0),
            'kindId': product.get('kindId', 0),
            'subjectId': product.get('subjectId', 0),
            'subjectParentId': product.get('subjectParentId', 0),
            'name': product.get('name', ''),
            'brand': product.get('brand', ''),
            'brandId': product.get('brandId'),
            'siteBrandId': product.get('siteBrandId'),
            'supplierId': product.get('supplierId'),
            'supplier': product.get('supplier', ''),
            'supplierRating': product.get('supplierRating', 0),
            'priceU': product.get('priceU', 0) / 100,
            'salePriceU': product.get('salePriceU', 0) / 100,
            'basicSale': product.get('basicSale', 0),
            'basicPriceU': product.get('basicPriceU', 0) / 100,
            'promoSale': product.get('promoSale', 0),
            'promoPriceU': product.get('promoPriceU', 0) / 100,
            'logisticsCost': product.get('logisticsCost', 0) / 100,
            'sale': product.get('sale', 0),
            'diffPrice': product.get('diffPrice', False),
            'promoTextCard': product.get('promoTextCard', ''),
            'promoTextCat': product.get('promoTextCat', ''),
            'rating': product.get('rating', 0),
            'feedbacks': product.get('feedbacks', 0),
            'reviewRating': product.get('reviewRating', 0),
            'pics': product.get('pics', 0),
            'volume': product.get('volume', 0),
            'viewFlags': product.get('viewFlags', 0),
            'time1': product.get('time1', 0),
            'time2': product.get('time2', 0),
            'wh': product.get('wh', 0),
            'dtype': product.get('dtype', 0),
            'colors': '; '.join(colors),
            'color_ids': '; '.join(map(str, color_ids)),
            'sizes': '; '.join(sizes),
            'size_ids': '; '.join(size_ids),
            'url': f"https://www.wildberries.ru/catalog/{product.get('id')}/detail.aspx",
            'timestamp': datetime.now().isoformat(),
            'characteristics': json.dumps(characteristics, ensure_ascii=False),
        }
    
    def get_product_details(self, product_id: str, full_characteristics: bool = True) -> Dict:
        """Получение детальной информации о товаре"""
        if full_characteristics:
            return self.card_parser.get_all_card_characteristics(product_id)
        else:
            return self._get_basic_product_details(product_id)
    
    def _get_basic_product_details(self, product_id: str) -> Dict:
        """Получение базовой информации о товаре"""
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
            return {'error': 'Не удалось получить данные', 'product_id': product_id}
        
        try:
            data = response.json()
            product = data.get('data', {}).get('products', [{}])[0]
            
            return {
                'product_id': product_id,
                'name': product.get('name', ''),
                'brand': product.get('brand', ''),
                'price': product.get('priceU', 0) / 100,
                'sale_price': product.get('salePriceU', 0) / 100,
                'rating': product.get('rating', 0),
                'feedbacks': product.get('feedbacks', 0),
                'supplier': product.get('supplier', ''),
                'url': f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx",
            }
        except Exception as e:
            return {'error': str(e), 'product_id': product_id}

# ==================== ОСНОВНОЙ ИНТЕРФЕЙС STREAMLIT ====================

def main():
    st.set_page_config(
        page_title="Продвинутый парсинг Wildberries 2026",
        layout="wide",
        initial_sidebar_state="expanded",
        page_icon="🛍️"
    )
    
    # Инициализация стилей
    st.markdown("""
    <style>
    .stProgress > div > div > div > div {
        background-color: #4F81BD;
    }
    .metric-container {
        background-color: #f0f2f6;
        padding: 10px;
        border-radius: 5px;
        border-left: 4px solid #4F81BD;
    }
    .success-box {
        background-color: #d4edda;
        border: 1px solid #c3e6cb;
        border-radius: 5px;
        padding: 15px;
        margin: 10px 0;
    }
    .warning-box {
        background-color: #fff3cd;
        border: 1px solid #ffeaa7;
        border-radius: 5px;
        padding: 15px;
        margin: 10px 0;
    }
    </style>
    """, unsafe_allow_html=True)
    
    st.title("🛍️ Продвинутый парсинг Wildberries 2026")
    st.markdown("---")
    
    # Инициализация конфигурации
    if 'config' not in st.session_state:
        st.session_state.config = Config().config
    
    # Инициализация клиента
    if 'wb_client' not in st.session_state:
        st.session_state.wb_client = WBApiClient(st.session_state.config)
    
    if 'parsed_data' not in st.session_state:
        st.session_state.parsed_data = pd.DataFrame()
    
    # Боковая панель
    with st.sidebar:
        st.header("⚙️ Настройки парсинга")
        
        search_mode = st.radio(
            "Режим работы",
            ["Поиск по запросу", "Парсинг продавца", "По артикулам"],
            help="Выберите режим в зависимости от задачи",
            index=0
        )
        
        st.subheader("📝 Параметры поиска")
        
        if search_mode == "Поиск по запросу":
            query = st.text_input("Поисковый запрос", value="ноутбук")
            pages = st.slider("Количество страниц", 1, 50, 3)
            products_per_page = st.select_slider(
                "Товаров на странице",
                options=[50, 100, 150, 200, 300],
                value=100
            )
            
            sort_options = {
                "Популярность": "popular",
                "Рейтинг": "rate",
                "Цена по возрастанию": "priceup",
                "Цена по убыванию": "pricedown",
                "Новинки": "newly",
                "Выгодные": "benefit"
            }
            
            sort_by = st.selectbox(
                "Сортировка",
                list(sort_options.keys()),
                index=0
            )
            
            category = st.selectbox(
                "Категория (опционально)",
                ["Автоопределение", "Электроника", "Одежда", "Дом", "Красота", "Детские", "Спорт"],
                index=0
            )
            
        elif search_mode == "Парсинг продавца":
            seller_id = st.text_input(
                "ID продавца",
                placeholder="Например: 123456",
                help="ID продавца можно найти в URL товара (параметр supplier)"
            )
            max_products = st.slider(
                "Максимальное количество товаров",
                10, 5000, 200,
                step=10
            )
            
        else:  # По артикулам
            articles_input = st.text_area(
                "Введите артикулы (через запятую или каждый с новой строки)",
                placeholder="12345678, 87654321\n98765432",
                height=100
            )
            articles = [art.strip() for art in articles_input.replace('\n', ',').split(',') if art.strip()] if articles_input else []
            
            if articles:
                st.info(f"Найдено артикулов: {len(articles)}")
        
        st.subheader("🔧 Дополнительные настройки")
        
        col1, col2 = st.columns(2)
        
        with col1:
            parse_details = st.checkbox("Собирать детальную информацию", value=True)
            full_characteristics = st.checkbox(
                "Полный парсинг характеристик", 
                value=True,
                help="Собирает ВСЕ характеристики из карточки товара"
            )
        
        with col2:
            use_proxies = st.checkbox("Использовать прокси", value=True)
            use_cache = st.checkbox("Использовать кэш", value=True)
        
        mode = st.selectbox(
            "Режим скорости",
            ["Сбалансированный", "Быстрый", "Медленный"],
            help="Быстрый - риск блокировки, Медленный - максимальная безопасность"
        )
        
        if mode == "Быстрый":
            delay_range = (0.5, 1.0)
            max_workers = 10
        elif mode == "Медленный":
            delay_range = (2.0, 4.0)
            max_workers = 2
        else:
            delay_range = (1.0, 2.0)
            max_workers = 5
        
        # Обновляем конфигурацию
        st.session_state.config['parsing']['delay_min'] = delay_range[0]
        st.session_state.config['parsing']['delay_max'] = delay_range[1]
        st.session_state.config['parsing']['max_workers'] = max_workers
        st.session_state.config['parsing']['use_proxies'] = use_proxies
        st.session_state.config['api']['enable_cache'] = use_cache
        
        st.subheader("💾 Настройки экспорта")
        export_format = st.selectbox("Формат экспорта", ["Excel", "CSV", "JSON", "Все форматы"])
        include_stats = st.checkbox("Включать статистику", value=True)
        include_charts = st.checkbox("Включать графики", value=True)
    
    # Основная область
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 Парсинг", "📈 Аналитика", "🔍 Детали", "📋 Данные", "💾 Экспорт"])
    
    with tab1:
        st.header("Запуск парсинга")
        
        # Показать текущую конфигурацию
        with st.expander("📋 Текущая конфигурация"):
            config_cols = st.columns(3)
            with config_cols[0]:
                st.metric("Задержка", f"{delay_range[0]}-{delay_range[1]} сек")
            with config_cols[1]:
                st.metric("Воркеры", max_workers)
            with config_cols[2]:
                status = "✅ Включен" if use_proxies else "❌ Выключен"
                st.metric("Прокси", status)
        
        col1, col2 = st.columns([3, 1])
        
        with col1:
            if search_mode == "Парсинг продавца" and seller_id:
                parse_button = st.button(
                    "🚀 Парсить товары продавца",
                    type="primary",
                    use_container_width=True,
                    key="parse_seller"
                )
            elif search_mode == "Поиск по запросу" and query:
                parse_button = st.button(
                    "🚀 Начать поиск",
                    type="primary",
                    use_container_width=True,
                    key="parse_query"
                )
            elif search_mode == "По артикулам" and articles:
                parse_button = st.button(
                    f"🚀 Парсить {len(articles)} артикулов",
                    type="primary",
                    use_container_width=True,
                    key="parse_articles"
                )
            else:
                parse_button = st.button(
                    "🚀 Начать парсинг",
                    type="primary",
                    use_container_width=True,
                    disabled=True,
                    key="parse_disabled"
                )
        
        with col2:
            if not st.session_state.parsed_data.empty:
                if st.button("🗑️ Очистить данные", use_container_width=True):
                    st.session_state.parsed_data = pd.DataFrame()
                    st.rerun()
        
        if parse_button:
            with st.spinner("Начинаем парсинг..."):
                try:
                    if search_mode == "Поиск по запросу":
                        all_results = []
                        progress_bar = st.progress(0)
                        
                        for page in range(1, pages + 1):
                            # Определяем категорию
                            cat_id = None
                            if category != "Автоопределение":
                                cat_map = {
                                    "Электроника": 17,
                                    "Одежда": 4,
                                    "Дом": 12,
                                    "Красота": 16,
                                    "Детские": 10,
                                    "Спорт": 9
                                }
                                cat_id = cat_map.get(category)
                            
                            df_page = st.session_state.wb_client.search_products(
                                query=query,
                                page=page,
                                limit=products_per_page,
                                sort=sort_options[sort_by],
                                category=cat_id
                            )
                            
                            if not df_page.empty:
                                all_results.append(df_page)
                                st.info(f"Страница {page}: найдено {len(df_page)} товаров")
                            
                            progress_bar.progress(page / pages)
                            time.sleep(random.uniform(*delay_range))
                        
                        if all_results:
                            df = pd.concat(all_results, ignore_index=True)
                            
                            if parse_details and not df.empty:
                                with st.spinner("Сбор детальной информации..."):
                                    detail_progress = st.progress(0)
                                    details_list = []
                                    
                                    product_ids = df['id'].astype(str).tolist()
                                    for idx, product_id in enumerate(product_ids):
                                        if full_characteristics:
                                            details = st.session_state.wb_client.get_product_details(
                                                product_id, 
                                                full_characteristics=True
                                            )
                                        else:
                                            details = st.session_state.wb_client.get_product_details(
                                                product_id, 
                                                full_characteristics=False
                                            )
                                        
                                        details_list.append(details)
                                        
                                        if idx % 5 == 0:
                                            detail_progress.progress((idx + 1) / len(product_ids))
                                        
                                        time.sleep(random.uniform(*delay_range))
                                    
                                    detail_progress.progress(1.0)
                                    
                                    # Преобразуем детали в DataFrame и объединяем
                                    if details_list:
                                        details_df = pd.DataFrame(details_list)
                                        df = pd.merge(df, details_df, left_on='id', right_on='product_id', how='left')
                            
                            st.session_state.parsed_data = df
                            
                            st.success(f"""
                            <div class="success-box">
                            <h4>✅ Успешно собраны данные!</h4>
                            <p><strong>Статистика:</strong></p>
                            <ul>
                            <li>Товаров: {len(df)}</li>
                            <li>Уникальных брендов: {df['brand'].nunique() if 'brand' in df.columns else 0}</li>
                            <li>Характеристик собрано: {len(df.columns) - 15} различных параметров</li>
                            </ul>
                            </div>
                            """, unsafe_allow_html=True)
                            
                        else:
                            st.warning("Не найдено товаров по данному запросу")
                    
                    elif search_mode == "Парсинг продавца":
                        with st.spinner(f"Парсинг товаров продавца {seller_id}..."):
                            # Для парсинга продавца используем многопоточность
                            from concurrent.futures import ThreadPoolExecutor
                            
                            # Сначала получаем список товаров продавца
                            st.info("Получение списка товаров продавца...")
                            
                            # Метод для получения товаров продавца
                            def get_seller_products():
                                url = "https://catalog.wb.ru/sellers/catalog"
                                all_products = []
                                page = 1
                                
                                while True:
                                    params = {
                                        'appType': 1,
                                        'curr': 'rub',
                                        'dest': -1257786,
                                        'lang': 'ru',
                                        'locale': 'ru',
                                        'page': page,
                                        'sort': 'popular',
                                        'spp': 30,
                                        'supplier': seller_id,
                                    }
                                    
                                    response = st.session_state.wb_client.request_manager.make_request(url, params=params)
                                    if not response or response.status_code != 200:
                                        break
                                    
                                    data = response.json()
                                    products = data.get('data', {}).get('products', [])
                                    
                                    if not products:
                                        break
                                    
                                    all_products.extend(products)
                                    
                                    if len(products) < 100 or len(all_products) >= max_products:
                                        break
                                    
                                    page += 1
                                    time.sleep(random.uniform(*delay_range))
                                
                                return all_products[:max_products]
                            
                            seller_products = get_seller_products()
                            
                            if not seller_products:
                                st.error("Не удалось получить товары продавца. Проверьте ID продавца.")
                            else:
                                st.info(f"Найдено {len(seller_products)} товаров продавца")
                                
                                # Собираем детальную информацию
                                progress_bar = st.progress(0)
                                all_details = []
                                
                                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                                    futures = []
                                    for product in seller_products:
                                        product_id = str(product.get('id'))
                                        future = executor.submit(
                                            st.session_state.wb_client.get_product_details,
                                            product_id,
                                            full_characteristics
                                        )
                                        futures.append(future)
                                    
                                    for idx, future in enumerate(as_completed(futures)):
                                        try:
                                            details = future.result(timeout=30)
                                            all_details.append(details)
                                        except Exception as e:
                                            logger.error(f"Ошибка получения деталей товара: {e}")
                                            continue
                                        
                                        if idx % 10 == 0:
                                            progress_bar.progress((idx + 1) / len(seller_products))
                                
                                progress_bar.progress(1.0)
                                
                                if all_details:
                                    df = pd.DataFrame(all_details)
                                    st.session_state.parsed_data = df
                                    
                                    st.success(f"""
                                    <div class="success-box">
                                    <h4>✅ Успешно собраны данные продавца!</h4>
                                    <p><strong>Статистика:</strong></p>
                                    <ul>
                                    <li>Товаров: {len(df)}</li>
                                    <li>Уникальных брендов: {df['brand'].nunique() if 'brand' in df.columns else 0}</li>
                                    <li>Характеристик собрано: {len(df.columns)} различных параметров</li>
                                    <li>Процент успеха: {len(df) / len(seller_products) * 100:.1f}%</li>
                                    </ul>
                                    </div>
                                    """, unsafe_allow_html=True)
                                else:
                                    st.error("Не удалось собрать данные по товарам продавца")
                    
                    elif search_mode == "По артикулам":
                        if not articles:
                            st.warning("Введите хотя бы один артикул")
                        else:
                            all_results = []
                            progress_bar = st.progress(0)
                            
                            for idx, article in enumerate(articles):
                                # Очищаем артикул
                                clean_article = re.sub(r'\D', '', article)
                                if clean_article:
                                    try:
                                        # Ищем товар
                                        df_art = st.session_state.wb_client.search_products(
                                            query=clean_article,
                                            page=1,
                                            limit=1
                                        )
                                        
                                        if not df_art.empty:
                                            if parse_details:
                                                details = st.session_state.wb_client.get_product_details(
                                                    clean_article,
                                                    full_characteristics=full_characteristics
                                                )
                                                # Объединяем данные
                                                for key, value in details.items():
                                                    df_art[key] = value
                                            
                                            all_results.append(df_art)
                                            st.info(f"Артикул {clean_article}: найден")
                                        else:
                                            st.warning(f"Артикул {clean_article}: не найден")
                                    
                                    except Exception as e:
                                        st.error(f"Ошибка при обработке артикула {clean_article}: {e}")
                                
                                progress_bar.progress((idx + 1) / len(articles))
                                time.sleep(random.uniform(*delay_range))
                            
                            if all_results:
                                st.session_state.parsed_data = pd.concat(all_results, ignore_index=True)
                                st.success(f"✅ Найдено {len(st.session_state.parsed_data)} товаров из {len(articles)} артикулов")
                            else:
                                st.warning("Не найдено ни одного товара по указанным артикулам")
                
                except Exception as e:
                    st.error(f"❌ Ошибка при парсинге: {str(e)}")
                    logger.exception("Ошибка парсинга")
    
    # Отображение данных
    if not st.session_state.parsed_data.empty:
        df = st.session_state.parsed_data
        
        with tab2:
            st.header("📊 Аналитика данных")
            
            # Основная статистика
            st.subheader("📈 Основные показатели")
            
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Всего товаров", len(df))
            with col2:
                if 'priceU' in df.columns:
                    avg_price = df['priceU'].mean()
                    st.metric("Средняя цена", f"{avg_price:,.0f} ₽")
                elif 'price' in df.columns:
                    avg_price = df['price'].mean()
                    st.metric("Средняя цена", f"{avg_price:,.0f} ₽")
            with col3:
                if 'feedbacks' in df.columns:
                    total_feedbacks = df['feedbacks'].sum()
                    st.metric("Всего отзывов", f"{total_feedbacks:,}")
            with col4:
                if 'brand' in df.columns:
                    unique_brands = df['brand'].nunique()
                    st.metric("Уникальных брендов", unique_brands)
            
            # Расширенная статистика
            st.subheader("📊 Расширенная аналитика")
            
            col1, col2 = st.columns(2)
            
            with col1:
                if 'priceU' in df.columns or 'price' in df.columns:
                    price_col = 'priceU' if 'priceU' in df.columns else 'price'
                    st.subheader("Распределение цен")
                    fig, ax = plt.subplots(figsize=(10, 6))
                    sns.histplot(df[price_col], bins=30, kde=True, ax=ax, color='skyblue')
                    ax.set_xlabel("Цена, руб")
                    ax.set_ylabel("Количество товаров")
                    ax.grid(True, alpha=0.3)
                    st.pyplot(fig)
            
            with col2:
                if 'rating' in df.columns:
                    st.subheader("Распределение рейтингов")
                    fig, ax = plt.subplots(figsize=(10, 6))
                    sns.histplot(df['rating'], bins=20, kde=True, ax=ax, color='lightcoral')
                    ax.set_xlabel("Рейтинг")
                    ax.set_ylabel("Количество товаров")
                    ax.set_xlim(0, 5)
                    ax.grid(True, alpha=0.3)
                    st.pyplot(fig)
            
            # Топ брендов
            if 'brand' in df.columns:
                st.subheader("🏆 Топ-10 брендов")
                top_brands = df['brand'].value_counts().head(10)
                
                if not top_brands.empty:
                    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
                    
                    # Столбчатая диаграмма
                    sns.barplot(x=top_brands.values, y=top_brands.index, ax=ax1, palette='viridis')
                    ax1.set_xlabel("Количество товаров")
                    ax1.set_title("Количество товаров по брендам")
                    
                    # Круговая диаграмма
                    ax2.pie(top_brands.values, labels=top_brands.index, autopct='%1.1f%%', startangle=90)
                    ax2.set_title("Доля брендов")
                    
                    plt.tight_layout()
                    st.pyplot(fig)
            
            # Анализ скидок
            if 'sale' in df.columns:
                st.subheader("💰 Анализ скидок")
                sale_counts = df['sale'].apply(lambda x: 'Со скидкой' if x > 0 else 'Без скидки').value_counts()
                
                if not sale_counts.empty:
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        fig, ax = plt.subplots(figsize=(8, 6))
                        ax.pie(sale_counts.values, labels=sale_counts.index, autopct='%1.1f%%', 
                              colors=['lightcoral', 'lightgreen'], startangle=90)
                        ax.set_title("Распределение товаров по наличию скидки")
                        st.pyplot(fig)
                    
                    with col2:
                        if sale_counts.get('Со скидкой', 0) > 0:
                            avg_sale = df[df['sale'] > 0]['sale'].mean()
                            max_sale = df[df['sale'] > 0]['sale'].max()
                            st.metric("Средний размер скидки", f"{avg_sale:.1f}%")
                            st.metric("Максимальная скидка", f"{max_sale:.1f}%")
        
        with tab3:
            st.header("🔍 Детальная информация")
            
            if not df.empty:
                # Выбор товара для детального просмотра
                st.subheader("Выберите товар для детального просмотра")
                
                # Создаем список для выбора
                product_options = []
                for idx, row in df.iterrows():
                    product_name = row.get('name', f'Товар {idx}')
                    if len(product_name) > 100:
                        product_name = product_name[:100] + '...'
                    
                    product_id = row.get('id', row.get('product_id', idx))
                    product_options.append(f"{product_id} - {product_name}")
                
                selected_product = st.selectbox(
                    "Выберите товар",
                    product_options,
                    index=0
                )
                
                if selected_product:
                    # Извлекаем ID товара
                    product_id = selected_product.split(' - ')[0]
                    
                    # Находим товар в DataFrame
                    if 'id' in df.columns:
                        product_row = df[df['id'].astype(str) == product_id]
                    elif 'product_id' in df.columns:
                        product_row = df[df['product_id'].astype(str) == product_id]
                    else:
                        product_row = df.iloc[[int(product_id)]]
                    
                    if not product_row.empty:
                        product = product_row.iloc[0]
                        
                        # Отображаем информацию в двух колонках
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            st.subheader("📋 Основная информация")
                            
                            basic_info = {
                                'ID': product.get('id', product.get('product_id', 'N/A')),
                                'Название': product.get('name', 'N/A'),
                                'Бренд': product.get('brand', 'N/A'),
                                'Артикул': product.get('article', 'N/A'),
                            }
                            
                            for key, value in basic_info.items():
                                if value != 'N/A':
                                    st.write(f"**{key}:** {value}")
                            
                            # Цены
                            st.subheader("💰 Цены")
                            price_info = {}
                            
                            if 'priceU' in product and pd.notna(product['priceU']):
                                price_info['Цена'] = f"{product['priceU']:,.2f} ₽"
                            if 'salePriceU' in product and pd.notna(product['salePriceU']):
                                price_info['Цена со скидкой'] = f"{product['salePriceU']:,.2f} ₽"
                            if 'sale' in product and pd.notna(product['sale']) and product['sale'] > 0:
                                price_info['Скидка'] = f"{product['sale']}%"
                            
                            for key, value in price_info.items():
                                st.write(f"**{key}:** {value}")
                        
                        with col2:
                            st.subheader("📊 Рейтинги и отзывы")
                            
                            rating_info = {}
                            if 'rating' in product and pd.notna(product['rating']):
                                rating_info['Рейтинг'] = f"{product['rating']:.2f}/5"
                            if 'feedbacks' in product and pd.notna(product['feedbacks']):
                                rating_info['Отзывов'] = f"{int(product['feedbacks']):,}"
                            if 'reviewRating' in product and pd.notna(product['reviewRating']):
                                rating_info['Рейтинг отзывов'] = f"{product['reviewRating']:.2f}"
                            
                            for key, value in rating_info.items():
                                st.write(f"**{key}:** {value}")
                            
                            # Ссылка на товар
                            if 'url' in product and product['url']:
                                st.markdown(f"[🔗 Открыть на Wildberries]({product['url']})")
                            
                            # Информация о продавце
                            st.subheader("🏪 Продавец")
                            seller_info = {}
                            if 'supplier' in product and product['supplier']:
                                seller_info['Продавец'] = product['supplier']
                            if 'supplierRating' in product and pd.notna(product['supplierRating']):
                                seller_info['Рейтинг продавца'] = product['supplierRating']
                            
                            for key, value in seller_info.items():
                                st.write(f"**{key}:** {value}")
                        
                        # Характеристики товара
                        st.subheader("🔧 Характеристики товара")
                        
                        # Ищем колонки с характеристиками
                        characteristic_cols = [col for col in df.columns 
                                             if col not in ['id', 'product_id', 'name', 'brand', 'priceU', 'salePriceU', 
                                                           'rating', 'feedbacks', 'url', 'timestamp', 'supplier',
                                                           'parse_status', 'error_message']]
                        
                        # Группируем характеристики
                        basic_chars = ['color', 'size', 'weight', 'material', 'country', 'dimensions']
                        other_chars = [col for col in characteristic_cols if col not in basic_chars]
                        
                        # Основные характеристики
                        with st.expander("📋 Основные характеристики", expanded=True):
                            cols = st.columns(3)
                            char_count = 0
                            
                            for char_key in basic_chars:
                                if char_key in product and pd.notna(product[char_key]) and product[char_key] != '':
                                    col_idx = char_count % 3
                                    with cols[col_idx]:
                                        st.write(f"**{char_key.replace('_', ' ').title()}:**")
                                        st.write(product[char_key])
                                    char_count += 1
                        
                        # Остальные характеристики
                        if other_chars:
                            with st.expander("📋 Все характеристики", expanded=False):
                                char_data = []
                                for char_key in other_chars:
                                    if char_key in product and pd.notna(product[char_key]) and product[char_key] != '':
                                        char_data.append({
                                            'Характеристика': char_key.replace('_', ' ').title(),
                                            'Значение': product[char_key]
                                        })
                                
                                if char_data:
                                    st.table(pd.DataFrame(char_data))
                                else:
                                    st.info("Нет дополнительных характеристик")
        
        with tab4:
            st.header("📋 Таблица данных")
            
            # Фильтрация
            st.subheader("🔍 Фильтрация данных")
            
            filter_cols = st.columns(4)
            
            with filter_cols[0]:
                if 'brand' in df.columns:
                    brands = ['Все'] + sorted(df['brand'].dropna().unique().tolist())
                    selected_brand = st.selectbox("Бренд", brands, key="filter_brand")
            
            with filter_cols[1]:
                # Определяем колонку с ценой
                price_cols = [col for col in ['priceU', 'salePriceU', 'price', 'sale_price'] if col in df.columns]
                if price_cols:
                    price_col = price_cols[0]
                    min_price = float(df[price_col].min())
                    max_price = float(df[price_col].max())
                    price_range = st.slider(
                        "Диапазон цен",
                        min_price, max_price, (min_price, max_price),
                        key="filter_price"
                    )
            
            with filter_cols[2]:
                if 'rating' in df.columns:
                    min_rating = float(df['rating'].min())
                    max_rating = float(df['rating'].max())
                    rating_range = st.slider(
                        "Рейтинг",
                        min_rating, max_rating, (min_rating, max_rating),
                        step=0.1,
                        key="filter_rating"
                    )
            
            with filter_cols[3]:
                if 'feedbacks' in df.columns:
                    min_feedbacks = int(df['feedbacks'].min())
                    max_feedbacks = int(df['feedbacks'].max())
                    feedbacks_range = st.slider(
                        "Количество отзывов",
                        min_feedbacks, max_feedbacks, (min_feedbacks, max_feedbacks),
                        key="filter_feedbacks"
                    )
            
            # Применяем фильтры
            filtered_df = df.copy()
            
            if 'brand' in df.columns and selected_brand != 'Все':
                filtered_df = filtered_df[filtered_df['brand'] == selected_brand]
            
            if price_cols:
                filtered_df = filtered_df[
                    (filtered_df[price_col] >= price_range[0]) & 
                    (filtered_df[price_col] <= price_range[1])
                ]
            
            if 'rating' in df.columns:
                filtered_df = filtered_df[
                    (filtered_df['rating'] >= rating_range[0]) & 
                    (filtered_df['rating'] <= rating_range[1])
                ]
            
            if 'feedbacks' in df.columns:
                filtered_df = filtered_df[
                    (filtered_df['feedbacks'] >= feedbacks_range[0]) & 
                    (filtered_df['feedbacks'] <= feedbacks_range[1])
                ]
            
            st.info(f"Найдено товаров после фильтрации: {len(filtered_df)}")
            
            # Выбор колонок для отображения
            st.subheader("🎯 Настройка отображения")
            
            # Группы колонок
            basic_cols = ['id', 'product_id', 'name', 'brand', 'priceU', 'salePriceU', 'sale', 'rating', 'feedbacks']
            char_cols = [col for col in df.columns if col not in basic_cols and col not in ['url', 'timestamp']]
            
            selected_groups = st.multiselect(
                "Выберите группы колонок",
                ['Основные', 'Характеристики', 'Все'],
                default=['Основные']
            )
            
            display_cols = []
            
            if 'Все' in selected_groups:
                display_cols = df.columns.tolist()
            else:
                if 'Основные' in selected_groups:
                    display_cols.extend([col for col in basic_cols if col in df.columns])
                if 'Характеристики' in selected_groups:
                    display_cols.extend([col for col in char_cols if col in df.columns])
            
            # Дополнительная фильтрация колонок
            if display_cols:
                selected_columns = st.multiselect(
                    "Выберите конкретные колонки",
                    display_cols,
                    default=display_cols[:min(10, len(display_cols))]
                )
                
                if selected_columns:
                    st.dataframe(
                        filtered_df[selected_columns],
                        use_container_width=True,
                        height=600
                    )
                else:
                    st.warning("Выберите хотя бы одну колонку для отображения")
            else:
                st.warning("Нет доступных колонок для отображения")
        
        with tab5:
            st.header("💾 Экспорт данных")
            
            if not df.empty:
                # Статистика данных
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    st.metric("Строк", len(df))
                with col2:
                    st.metric("Колонок", len(df.columns))
                with col3:
                    non_null_cols = df.count().sum()
                    total_cells = len(df) * len(df.columns)
                    st.metric("Заполненность", f"{(non_null_cols / total_cells * 100):.1f}%")
                with col4:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                
                # Настройки экспорта
                st.subheader("⚙️ Настройки экспорта")
                
                export_cols = st.columns(3)
                
                with export_cols[0]:
                    file_prefix = st.text_input(
                        "Префикс имени файла",
                        value="wb_data"
                    )
                
                with export_cols[1]:
                    # Определяем дополнительную информацию для имени файла
                    if search_mode == "Поиск по запросу" and 'query' in locals():
                        extra_info = query[:20]
                    elif search_mode == "Парсинг продавца" and 'seller_id' in locals():
                        extra_info = f"seller_{seller_id}"
                    elif search_mode == "По артикулам" and articles:
                        extra_info = f"articles_{len(articles)}"
                    else:
                        extra_info = "data"
                    
                    file_name = f"{file_prefix}_{extra_info}_{timestamp}"
                
                with export_cols[2]:
                    compression = st.checkbox("Сжатие", value=True)
                
                # Кнопки экспорта
                st.subheader("📥 Экспорт в форматы")
                
                export_buttons = st.columns(4)
                
                def prepare_excel():
                    """Подготовка Excel файла"""
                    output = BytesIO()
                    
                    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                        # Основной лист
                        df.to_excel(writer, sheet_name='Данные', index=False)
                        
                        if include_stats:
                            # Лист со статистикой
                            stats_df = pd.DataFrame({
                                'Параметр': ['Всего товаров', 'Уникальных брендов', 'Дата сбора'],
                                'Значение': [len(df), df['brand'].nunique() if 'brand' in df.columns else 0, 
                                           datetime.now().strftime("%d.%m.%Y %H:%M")]
                            })
                            stats_df.to_excel(writer, sheet_name='Статистика', index=False)
                        
                        # Автоподбор ширины колонок
                        worksheet = writer.sheets['Данные']
                        for i, col in enumerate(df.columns):
                            column_width = max(df[col].astype(str).map(len).max(), len(col)) + 2
                            worksheet.set_column(i, i, min(column_width, 50))
                    
                    output.seek(0)
                    return output
                
                with export_buttons[0]:
                    if st.button("📊 Excel", use_container_width=True):
                        excel_data = prepare_excel()
                        st.download_button(
                            label="Скачать Excel",
                            data=excel_data,
                            file_name=f"{file_name}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                
                with export_buttons[1]:
                    csv_data = df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
                    st.download_button(
                        label="📄 CSV (UTF-8)",
                        data=csv_data,
                        file_name=f"{file_name}.csv",
                        mime="text/csv",
                        use_container_width=True
                    )
                
                with export_buttons[2]:
                    json_data = df.to_json(orient='records', force_ascii=False, indent=2).encode('utf-8')
                    st.download_button(
                        label="🔤 JSON",
                        data=json_data,
                        file_name=f"{file_name}.json",
                        mime="application/json",
                        use_container_width=True
                    )
                
                with export_buttons[3]:
                    # Экспорт в SQLite
                    sqlite_buffer = BytesIO()
                    conn = sqlite3.connect(':memory:')
                    df.to_sql('products', conn, if_exists='replace', index=False)
                    
                    # Сохраняем в файл
                    backup = sqlite3.connect(sqlite_buffer)
                    conn.backup(backup)
                    backup.close()
                    
                    sqlite_buffer.seek(0)
                    
                    st.download_button(
                        label="🗃️ SQLite",
                        data=sqlite_buffer,
                        file_name=f"{file_name}.db",
                        mime="application/x-sqlite3",
                        use_container_width=True
                    )
                
                # Предварительный просмотр
                st.subheader("👁️ Предварительный просмотр")
                
                preview_cols = st.columns([1, 3])
                
                with preview_cols[0]:
                    preview_rows = st.slider("Строк для предпросмотра", 5, 50, 10)
                
                with preview_cols[1]:
                    st.dataframe(
                        df.head(preview_rows),
                        use_container_width=True,
                        height=300
                    )
                
                # Информация о данных
                with st.expander("📊 Информация о данных"):
                    st.write("**Типы данных:**")
                    st.write(df.dtypes)
                    
                    st.write("**Пропущенные значения:**")
                    missing = df.isnull().sum()
                    st.write(missing[missing > 0])
                    
    else:
        # Инструкция при первом запуске
        with tab1:
            st.info("""
            ## 🎯 Инструкция по использованию
            
            ### 1. Выберите режим работы:
            
            **🔍 Поиск по запросу:**
            - Поиск товаров по ключевым словам
            - Можно указать категорию для более точного поиска
            - Настройте сортировку и количество страниц
            
            **🏪 Парсинг продавца:**
            - Сбор всех товаров конкретного продавца
            - Введите ID продавца (можно найти в URL товара)
            - Укажите максимальное количество товаров
            
            **📦 По артикулам:**
            - Поиск конкретных товаров по артикулам
            - Введите артикулы через запятую или с новой строки
            - Идеально для мониторинга конкретных товаров
            
            ### 2. Настройте параметры парсинга:
            
            **⚡ Режим скорости:**
            - Сбалансированный: оптимальная скорость и безопасность
            - Быстрый: риск блокировки, но быстро
            - Медленный: максимальная безопасность
            
            **🔧 Дополнительные настройки:**
            - Включите "Полный парсинг характеристик" для сбора всех данных
            - Используйте прокси для обхода блокировок
            - Включите кэш для ускорения повторных запросов
            
            ### 3. Нажмите кнопку "Начать парсинг"
            
            ### 4. Анализируйте и экспортируйте данные
            
            ## 📊 Что собирается:
            
            ✅ **Основные данные:**
            - Название, бренд, артикул
            - Цены (основная, со скидкой, размер скидки)
            - Рейтинги и отзывы
            - Информация о продавце
            
            ✅ **Характеристики товаров:**
            - Все параметры из карточки товара
            - Размеры, цвет, материал, страна производства
            - Технические характеристики
            - Состав и особенности
            
            ✅ **Дополнительная информация:**
            - Остатки на складах
            - Информация о доставке
            - Отзывы покупателей
            - SEO данные
            
            ## ⚠️ Рекомендации:
            
            1. **Начинайте с небольших объемов** (100-200 товаров)
            2. **Используйте задержки** между запросами
            3. **Включайте прокси** при парсинге больших объемов
            4. **Сохраняйте промежуточные результаты**
            5. **Проверяйте качество данных** перед анализом
            
            ## 🔧 Технические особенности:
            
            - Автоматическое определение категорий
            - Нормализация характеристик
            - Кэширование запросов
            - Многопоточная обработка
            - Обработка ошибок и повторные попытки
            
            ## 📈 Возможности анализа:
            
            - Статистика по ценам и рейтингам
            - Анализ брендов и продавцов
            - Визуализация данных
            - Фильтрация и поиск
            - Экспорт в различные форматы
            
            **Начните работу, выбрав режим и настройки выше!**
            """)

if __name__ == "__main__":
    # Информация о зависимостях
    requirements = """
    streamlit>=1.28.0
    pandas>=2.0.0
    requests>=2.31.0
    matplotlib>=3.7.0
    seaborn>=0.12.0
    xlsxwriter>=3.1.0
    fake-useragent>=1.4.0
    cloudscraper>=1.2.71
    undetected-chromedriver>=3.5.0
    fp.free-proxy>=1.1.0
    aiohttp>=3.9.0
    PyYAML>=6.0
    numpy>=1.24.0
    scikit-learn>=1.3.0
    """
    
    # Проверка зависимостей
    try:
        main()
    except ImportError as e:
        st.error(f"""
        ❌ Отсутствует необходимая библиотека: {e.name}
        
        Установите зависимости командой:
        ```bash
        pip install streamlit pandas requests matplotlib seaborn xlsxwriter
        pip install fake-useragent cloudscraper undetected-chromedriver
        pip install fp.free-proxy aiohttp PyYAML numpy scikit-learn
        ```
        
        Или создайте файл requirements.txt:
        ```txt
        {requirements}
        ```
        И установите:
        ```bash
        pip install -r requirements.txt
        ```
        """)
