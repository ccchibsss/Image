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
from typing import List, Dict, Optional, Tuple, Any
import logging
from fake_useragent import UserAgent
from concurrent.futures import ThreadPoolExecutor, as_completed
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import cloudscraper
#from freeproxy import FreeProxy
import asyncio
import aiohttp

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== КЛАСС ДЛЯ ОБХОДА ЗАЩИТЫ ====================

class ProxyManager:
    """Менеджер прокси для ротации"""
    def __init__(self):
        self.proxies = []
        self.current_index = 0
        self.last_update = datetime.now()
        
    def get_proxy(self):
        """Получение случайного прокси"""
        if not self.proxies or (datetime.now() - self.last_update).seconds > 300:
            self.update_proxies()
        
        if self.proxies:
            proxy = random.choice(self.proxies)
            return {
                'http': f'http://{proxy}',
                'https': f'http://{proxy}'
            }
        return None
    
    def update_proxies(self):
        """Обновление списка прокси"""
        try:
            proxy = FreeProxy(rand=True, timeout=1).get()
            if proxy:
                self.proxies = [proxy]
                self.last_update = datetime.now()
                logger.info(f"Обновлены прокси: {self.proxies}")
        except:
            pass

class RequestManager:
    """Управление запросами с ротацией параметров"""
    def __init__(self):
        self.ua = UserAgent()
        self.proxy_manager = ProxyManager()
        self.session = requests.Session()
        self.delay_range = (1, 3)
        self.request_count = 0
        self.last_request_time = datetime.now()
        
    def get_headers(self) -> Dict:
        """Генерация рандомизированных заголовков"""
        headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Connection': 'keep-alive',
            'DNT': '1',
            'Host': 'search.wb.ru',
            'Origin': 'https://www.wildberries.ru',
            'Referer': 'https://www.wildberries.ru/',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'cross-site',
            'User-Agent': self.ua.random,
            'sec-ch-ua': '"Chromium";v="118", "Google Chrome";v="118", "Not=A?Brand";v="99"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }
        return headers
    
    def make_request(self, url: str, method: str = 'GET', **kwargs) -> Optional[requests.Response]:
        """Выполнение запроса с задержками и ротацией"""
        # Случайная задержка
        delay = random.uniform(*self.delay_range)
        elapsed = (datetime.now() - self.last_request_time).seconds
        
        if elapsed < delay:
            time.sleep(delay - elapsed)
        
        # Ротация параметров
        proxy = self.proxy_manager.get_proxy()
        headers = self.get_headers()
        
        try:
            response = self.session.request(
                method=method,
                url=url,
                headers=headers,
                proxies=proxy,
                timeout=30,
                **kwargs
            )
            
            self.request_count += 1
            self.last_request_time = datetime.now()
            
            # Ротация сессии каждые 50 запросов
            if self.request_count % 50 == 0:
                self.session = requests.Session()
                logger.info("Сессия обновлена")
            
            return response
            
        except Exception as e:
            logger.error(f"Ошибка запроса: {e}")
            return None

class SeleniumParser:
    """Парсинг через Selenium для сложных случаев"""
    def __init__(self):
        self.driver = None
        
    def init_driver(self):
        """Инициализация Selenium драйвера"""
        options = uc.ChromeOptions()
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        
        # Случайный user-agent
        ua = UserAgent()
        options.add_argument(f'user-agent={ua.random}')
        
        # Скрытие WebDriver
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        
        try:
            self.driver = uc.Chrome(options=options, version_main=118)
            # Удаление WebDriver свойств
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        except:
            # Fallback на обычный Chrome
            from selenium import webdriver
            self.driver = webdriver.Chrome(options=options)
        
        return self.driver
    
    def parse_with_selenium(self, url: str) -> Optional[Dict]:
        """Парсинг страницы через Selenium"""
        if not self.driver:
            self.init_driver()
        
        try:
            self.driver.get(url)
            time.sleep(random.uniform(2, 4))
            
            # Имитация человеческого поведения
            self.simulate_human_behavior()
            
            # Ждем загрузку контента
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            # Получаем данные через JavaScript
            script = """
            return {
                title: document.title,
                url: window.location.href,
                description: document.querySelector('meta[name="description"]')?.content || '',
                price: document.querySelector('.price-block__price')?.innerText || '',
                rating: document.querySelector('.product-review__rating')?.innerText || '',
                characteristics: Array.from(document.querySelectorAll('.product-params__item')).map(item => {
                    const name = item.querySelector('.product-params__label')?.innerText || '';
                    const value = item.querySelector('.product-params__value')?.innerText || '';
                    return {name, value};
                })
            };
            """
            
            data = self.driver.execute_script(script)
            return data
            
        except Exception as e:
            logger.error(f"Ошибка Selenium парсинга: {e}")
            return None
    
    def simulate_human_behavior(self):
        """Имитация человеческого поведения на странице"""
        try:
            # Случайные движения мыши
            actions = [
                lambda: self.driver.execute_script("window.scrollBy(0, 300);"),
                lambda: self.driver.execute_script("window.scrollBy(0, -150);"),
                lambda: time.sleep(random.uniform(0.5, 1.5))
            ]
            
            for _ in range(random.randint(2, 5)):
                random.choice(actions)()
                
        except:
            pass
    
    def close(self):
        """Закрытие драйвера"""
        if self.driver:
            self.driver.quit()

# ==================== УЛУЧШЕННЫЙ ПАРСЕР ХАРАКТЕРИСТИК ====================

class EnhancedCardParser:
    """Расширенный парсер характеристик карточек товаров"""
    
    def __init__(self, request_manager: RequestManager):
        self.request_manager = request_manager
        self.ua = UserAgent()
        
    def get_all_card_characteristics(self, product_id: str) -> Dict[str, Any]:
        """Получение ВСЕХ характеристик из карточки товара"""
        characteristics_data = {}
        
        try:
            # Основной эндпоинт для характеристик
            char_url = f"https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&lang=ru&locale=ru&nm={product_id}&spp=30"
            
            response = self.request_manager.make_request(char_url)
            
            if not response or response.status_code != 200:
                return {'product_id': product_id, 'error': 'API недоступен'}
                
            data = response.json()
            product = data.get('data', {}).get('products', [{}])[0]
            
            # Базовая информация
            characteristics_data = {
                'product_id': product_id,
                'name': product.get('name', ''),
                'brand': product.get('brand', ''),
                'brandId': product.get('brandId'),
                'supplierId': product.get('supplierId'),
                'supplier': product.get('supplier', ''),
                'price': product.get('priceU', 0) / 100,
                'salePrice': product.get('salePriceU', 0) / 100,
                'sale': product.get('sale', 0),
                'rating': product.get('rating', 0),
                'feedbacks': product.get('feedbacks', 0),
                'reviewRating': product.get('reviewRating', 0),
                'pics': product.get('pics', 0),
                'colors': json.dumps(product.get('colors', []), ensure_ascii=False),
                'sizes': json.dumps(product.get('sizes', []), ensure_ascii=False),
                'diffPrice': product.get('diffPrice', False),
                'promoTextCard': product.get('promoTextCard', ''),
                'promoTextCat': product.get('promoTextCat', ''),
                'time1': product.get('time1', 0),
                'time2': product.get('time2', 0),
                'wh': product.get('wh', 0),
                'dtype': product.get('dtype', 0),
            }
            
            # Характеристики товара
            characteristics = {}
            for opt in product.get('options', []):
                name = opt.get('name', '').strip()
                value = opt.get('value', '').strip()
                if name and value:
                    characteristics[name] = value
            
            # Добавляем характеристики как отдельные колонки
            characteristics_data.update(characteristics)
            
            # Дополнительная информация из других эндпоинтов
            
            # 1. Информация о продавце и складах
            try:
                stock_url = f"https://product-order-qnt.wildberries.ru/by-nm/?nm={product_id}"
                stock_response = self.request_manager.make_request(stock_url)
                if stock_response and stock_response.status_code == 200:
                    stock_data = stock_response.json()
                    characteristics_data.update({
                        'stock_total': sum(item.get('qnt', 0) for item in stock_data),
                        'stock_details': json.dumps(stock_data, ensure_ascii=False)
                    })
            except:
                pass
            
            # 2. Категории и теги
            try:
                categories = product.get('categoryTree', [])
                if categories:
                    characteristics_data['category_full'] = ' > '.join([cat.get('name', '') for cat in categories])
                    characteristics_data['category_id'] = categories[-1].get('id') if categories else ''
            except:
                pass
            
            # 3. Рейтинги продавца
            try:
                seller_url = f"https://feedbacks1.wb.ru/feedbacks/v1/{product_id}"
                seller_response = self.request_manager.make_request(seller_url)
                if seller_response and seller_response.status_code == 200:
                    seller_data = seller_response.json()
                    characteristics_data.update({
                        'seller_feedbacks_total': seller_data.get('feedbackCount', 0),
                        'seller_rating': seller_data.get('valuation', ''),
                        'seller_feedbacks': json.dumps(seller_data.get('feedbacks', [])[:5], ensure_ascii=False)
                    })
            except:
                pass
            
            # 4. Информация о доставке
            try:
                basket_num = self._get_basket_number(product_id)
                delivery_url = f"https://basket-{basket_num}.wbbasket.ru/vol{product_id[:4]}/part{int(product_id)//1000}/{product_id}/info/ru/card.json"
                delivery_response = self.request_manager.make_request(delivery_url)
                if delivery_response and delivery_response.status_code == 200:
                    delivery_data = delivery_response.json()
                    characteristics_data.update({
                        'delivery_info': json.dumps(delivery_data.get('delivery', {}), ensure_ascii=False),
                        'guarantee_info': delivery_data.get('guarantee', ''),
                        'description': delivery_data.get('description', ''),
                        'compositions': json.dumps(delivery_data.get('compositions', []), ensure_ascii=False),
                        'certificates': json.dumps(delivery_data.get('certificates', []), ensure_ascii=False),
                    })
            except:
                pass
            
            # 5. Отзывы (первые 10)
            try:
                feedbacks_url = f"https://feedbacks2.wb.ru/feedbacks/v1/{product_id}"
                feedbacks_response = self.request_manager.make_request(feedbacks_url)
                if feedbacks_response and feedbacks_response.status_code == 200:
                    feedbacks_data = feedbacks_response.json()
                    characteristics_data['recent_feedbacks'] = json.dumps(
                        feedbacks_data.get('feedbacks', [])[:10], 
                        ensure_ascii=False
                    )
            except:
                pass
            
            # 6. SEO информация
            characteristics_data.update({
                'url': f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx",
                'timestamp': datetime.now().isoformat()
            })
            
            # Собираем все характеристики в одну строку для поиска
            all_chars_str = '; '.join([f"{k}: {v}" for k, v in characteristics.items()])
            characteristics_data['all_characteristics'] = all_chars_str
            
        except Exception as e:
            logger.error(f"Ошибка получения характеристик товара {product_id}: {e}")
            characteristics_data = {'product_id': product_id, 'error': str(e)}
        
        return characteristics_data
    
    def _get_basket_number(self, product_id: str) -> str:
        """Определение номера корзины"""
        try:
            num = int(product_id[-3:])
            baskets = [
                (0, 143, '01'), (144, 287, '02'), (288, 431, '03'),
                (432, 719, '04'), (720, 1007, '05'), (1008, 1061, '06'),
                (1062, 1115, '07'), (1116, 1169, '08'), (1170, 1313, '09'),
                (1314, 1601, '10'), (1602, 1655, '11'), (1656, 1919, '12'),
                (1920, 2045, '13')
            ]
            for start, end, basket in baskets:
                if start <= num <= end:
                    return basket
        except:
            pass
        return '01'
    
    def parse_seller_cards(self, seller_id: str, max_products: int = 500) -> pd.DataFrame:
        """Парсинг всех карточек товаров продавца"""
        logger.info(f"Начинаем парсинг продавца {seller_id}")
        
        # Получаем все товары продавца
        seller_parser = WBSellerParser(self.request_manager)
        products = seller_parser.get_seller_products(seller_id, max_products)
        
        if not products:
            return pd.DataFrame()
        
        logger.info(f"Найдено {len(products)} товаров продавца")
        
        # Собираем данные со всех товаров
        all_data = []
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(self.get_all_card_characteristics, str(p.get('id'))): p 
                for p in products[:max_products]
            }
            
            for i, future in enumerate(as_completed(futures)):
                try:
                    product_data = future.result(timeout=30)
                    all_data.append(product_data)
                    
                    if (i + 1) % 10 == 0:
                        logger.info(f"Обработано {i + 1}/{len(products)} товаров")
                    
                    # Случайная задержка между запросами
                    time.sleep(random.uniform(0.2, 0.5))
                    
                except Exception as e:
                    logger.error(f"Ошибка обработки товара: {e}")
                    continue
        
        # Создаем DataFrame
        if not all_data:
            return pd.DataFrame()
        
        df = pd.DataFrame(all_data)
        
        # Преобразуем JSON строки в читаемый вид
        json_columns = ['colors', 'sizes', 'stock_details', 'seller_feedbacks', 
                       'delivery_info', 'compositions', 'certificates', 'recent_feedbacks']
        
        for col in json_columns:
            if col in df.columns:
                df[col] = df[col].apply(lambda x: self._format_json(x) if pd.notna(x) else '')
        
        return df
    
    def _format_json(self, json_str: str) -> str:
        """Форматирование JSON строки для читаемости"""
        try:
            data = json.loads(json_str)
            return json.dumps(data, ensure_ascii=False, indent=2)
        except:
            return json_str

class WBSellerParser:
    """Парсер товаров продавца"""
    def __init__(self, request_manager: RequestManager):
        self.request_manager = request_manager
        self.ua = UserAgent()
        
    def get_seller_products(self, seller_id: str, limit: int = 1000) -> List[Dict]:
        """Получение всех товаров продавца по ID"""
        products = []
        page = 1
        
        while True:
            url = f"https://catalog.wb.ru/sellers/catalog?appType=1&curr=rub&dest=-1257786&lang=ru&locale=ru&page={page}&sort=popular&spp=30&supplier={seller_id}"
            
            response = self.request_manager.make_request(url)
            
            if not response or response.status_code != 200:
                break
                
            data = response.json()
            page_products = data.get('data', {}).get('products', [])
            
            if not page_products:
                break
                
            products.extend(page_products)
            logger.info(f"Страница {page}: получено {len(page_products)} товаров")
            
            # Проверяем, есть ли еще страницы
            if len(page_products) < 100 or page * 100 >= limit:
                break
                
            page += 1
            time.sleep(random.uniform(0.5, 1.5))
        
        return products[:limit]

# ==================== ОСНОВНОЙ КЛИЕНТ ====================

class WBApiClient:
    """Клиент для работы с API Wildberries"""
    def __init__(self):
        self.request_manager = RequestManager()
        self.cloud_scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'mobile': False
            }
        )
        self.card_parser = EnhancedCardParser(self.request_manager)
        
    def search_products(self, query: str, page: int = 1, limit: int = 100) -> pd.DataFrame:
        """Поиск товаров через разные методы"""
        # Метод 1: Официальное API
        results = self._search_api_v1(query, page, limit)
        
        if results.empty:
            # Метод 2: Cloudflare bypass
            results = self._search_cloudflare(query, page, limit)
        
        if results.empty:
            # Метод 3: Мобильное API
            results = self._search_mobile_api(query, page, limit)
        
        return results
    
    def _search_api_v1(self, query: str, page: int, limit: int) -> pd.DataFrame:
        """Поиск через основной API"""
        url = f"https://search.wb.ru/exactmatch/ru/common/v5/search?appType=1&curr=rub&dest=-1257786&lang=ru&page={page}&query={urllib.parse.quote(query)}&resultset=catalog&sort=popular&spp=30&limit={limit}"
        
        response = self.request_manager.make_request(url)
        if not response or response.status_code != 200:
            return pd.DataFrame()
        
        try:
            data = response.json()
            products = data.get('data', {}).get('products', [])
            return self._parse_products(products)
        except:
            return pd.DataFrame()
    
    def _search_cloudflare(self, query: str, page: int, limit: int) -> pd.DataFrame:
        """Обход Cloudflare через cloudscraper"""
        try:
            url = f"https://search.wb.ru/exactmatch/ru/common/v4/search?appType=1&curr=rub&dest=-1257786&lang=ru&page={page}&query={urllib.parse.quote(query)}&resultset=catalog&sort=popular&spp=30&limit={limit}"
            
            response = self.cloud_scraper.get(url, timeout=30)
            if response.status_code == 200:
                data = response.json()
                products = data.get('data', {}).get('products', [])
                return self._parse_products(products)
        except Exception as e:
            logger.error(f"Cloudflare bypass error: {e}")
        
        return pd.DataFrame()
    
    def _search_mobile_api(self, query: str, page: int, limit: int) -> pd.DataFrame:
        """Поиск через мобильное API"""
        url = f"https://mobile-cache.wb.ru/catalog?appType=1&curr=rub&dest=-1257786&lang=ru&page={page}&query={urllib.parse.quote(query)}&resultset=catalog&sort=popular&spp=30&limit={limit}"
        
        response = self.request_manager.make_request(url)
        if not response or response.status_code != 200:
            return pd.DataFrame()
        
        try:
            data = response.json()
            products = data.get('data', {}).get('products', [])
            return self._parse_products(products)
        except:
            return pd.DataFrame()
    
    def _parse_products(self, products: List[Dict]) -> pd.DataFrame:
        """Парсинг списка товаров"""
        items = []
        for p in products:
            try:
                item = {
                    'id': p.get('id'),
                    'article': p.get('id'),
                    'name': p.get('name', ''),
                    'brand': p.get('brand', ''),
                    'price': p.get('priceU', 0) / 100,
                    'sale_price': p.get('salePriceU', 0) / 100 if p.get('salePriceU') else p.get('priceU', 0) / 100,
                    'sale_percent': p.get('sale', 0),
                    'rating': p.get('rating', 0),
                    'feedbacks': p.get('feedbacks', 0),
                    'reviewRating': p.get('reviewRating', 0),
                    'supplier': p.get('supplier', ''),
                    'supplierId': p.get('supplierId'),
                    'in_stock': p.get('quantity', 0),
                    'category': p.get('category', ''),
                    'url': f"https://www.wildberries.ru/catalog/{p.get('id')}/detail.aspx",
                    'timestamp': datetime.now().isoformat()
                }
                items.append(item)
            except Exception as e:
                logger.error(f"Error parsing product: {e}")
                continue
        
        return pd.DataFrame(items)
    
    def get_product_details(self, product_id: str, full_characteristics: bool = True) -> Dict:
        """Получение детальной информации о товаре"""
        details = {}
        
        if full_characteristics:
            # Используем улучшенный парсер характеристик
            return self.card_parser.get_all_card_characteristics(product_id)
        else:
            # Оригинальный метод
            details.update(self._get_details_api(product_id))
            
            # Метод 2: Альтернативный эндпоинт
            if not details.get('characteristics'):
                details.update(self._get_details_alt(product_id))
            
            # Метод 3: Selenium для полной информации
            if not details.get('full_description'):
                selenium_parser = SeleniumParser()
                try:
                    html_data = selenium_parser.parse_with_selenium(
                        f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx"
                    )
                    if html_data:
                        details['html_data'] = html_data
                finally:
                    selenium_parser.close()
        
        return details
    
    def _get_details_api(self, product_id: str) -> Dict:
        """Получение деталей через основной API"""
        url = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={product_id}"
        
        response = self.request_manager.make_request(url)
        if not response or response.status_code != 200:
            return {}
        
        try:
            data = response.json()
            product = data.get('data', {}).get('products', [{}])[0]
            
            # Характеристики
            characteristics = {}
            for opt in product.get('options', []):
                characteristics[opt.get('name', '')] = opt.get('value', '')
            
            return {
                'characteristics': characteristics,
                'seller_info': {
                    'supplierId': product.get('supplierId'),
                    'supplier': product.get('supplier'),
                    'supplierRating': product.get('supplierRating'),
                },
                'stock_info': {
                    'total': product.get('total', 0),
                    'in_stock': product.get('in_stock', 0),
                }
            }
        except:
            return {}
    
    def _get_details_alt(self, product_id: str) -> Dict:
        """Альтернативный метод получения деталей"""
        urls = [
            f"https://basket-{self._get_basket_number(product_id)}.wbbasket.ru/vol{product_id[:4]}/part{int(product_id)//1000}/{product_id}/info/ru/card.json",
            f"https://product-order-qnt.wildberries.ru/by-nm/?nm={product_id}",
            f"https://feedbacks1.wb.ru/feedbacks/v1/{product_id}",
        ]
        
        for url in urls:
            try:
                response = self.request_manager.make_request(url)
                if response and response.status_code == 200:
                    data = response.json()
                    # Обработка данных в зависимости от эндпоинта
                    if 'feedbacks' in url:
                        return {'feedbacks_data': data.get('feedbacks', [])}
                    else:
                        return {'additional_data': data}
            except:
                continue
        
        return {}
    
    def _get_basket_number(self, product_id: str) -> str:
        """Определение номера корзины для товара"""
        try:
            num = int(product_id[-3:])
            if num <= 143:
                return "01"
            elif num <= 287:
                return "02"
            elif num <= 431:
                return "03"
            elif num <= 719:
                return "04"
            elif num <= 1007:
                return "05"
            elif num <= 1061:
                return "06"
            elif num <= 1115:
                return "07"
            elif num <= 1169:
                return "08"
            elif num <= 1313:
                return "09"
            elif num <= 1601:
                return "10"
            elif num <= 1655:
                return "11"
            elif num <= 1919:
                return "12"
            else:
                return "13"
        except:
            return "01"

# ==================== УЛУЧШЕННЫЙ ЭКСПОРТ ====================

class EnhancedExcelExporter:
    """Экспорт данных в Excel с характеристиками"""
    
    @staticmethod
    def export_to_excel(df: pd.DataFrame, filename: str = "wb_data.xlsx", include_stats: bool = True) -> BytesIO:
        """Экспорт в Excel с расширенным форматированием"""
        output = BytesIO()
        
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            # 1. Основной лист со всеми данными
            df.to_excel(writer, sheet_name='Все товары', index=False)
            
            # 2. Лист с характеристиками в широком формате
            EnhancedExcelExporter._create_wide_format_sheet(writer, df)
            
            if include_stats:
                # 3. Лист со статистикой
                EnhancedExcelExporter._create_stats_sheet(writer, df)
                
                # 4. Лист с выжимкой характеристик
                EnhancedExcelExporter._create_summary_sheet(writer, df)
            
            # Форматирование
            workbook = writer.book
            
            # Форматы
            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'top',
                'align': 'center',
                'bg_color': '#366092',
                'font_color': 'white',
                'border': 1,
                'font_size': 10
            })
            
            money_format = workbook.add_format({'num_format': '#,##0.00" ₽"'})
            percent_format = workbook.add_format({'num_format': '0.00%"'})
            int_format = workbook.add_format({'num_format': '#,##0'})
            rating_format = workbook.add_format({'num_format': '0.00'})
            date_format = workbook.add_format({'num_format': 'dd.mm.yyyy hh:mm'})
            
            # Применяем форматирование ко всем листам
            for sheet_name in writer.sheets:
                worksheet = writer.sheets[sheet_name]
                
                if sheet_name == 'Все товары':
                    # Автоширина колонок для основного листа
                    for col_num, column_name in enumerate(df.columns):
                        max_length = max(
                            df[column_name].astype(str).str.len().max(),
                            len(column_name)
                        ) + 2
                        worksheet.set_column(col_num, col_num, min(max_length, 50))
                        
                        # Заголовки
                        worksheet.write(0, col_num, column_name, header_format)
                        
                        # Форматирование числовых колонок
                        if any(keyword in column_name.lower() for keyword in ['price', 'цена', 'стоимость']):
                            worksheet.set_column(col_num, col_num, 15, money_format)
                        elif any(keyword in column_name.lower() for keyword in ['sale', 'скидка', 'percent']):
                            worksheet.set_column(col_num, col_num, 12, percent_format)
                        elif any(keyword in column_name.lower() for keyword in ['feedbacks', 'отзывы', 'quantity', 'количество', 'stock']):
                            worksheet.set_column(col_num, col_num, 12, int_format)
                        elif any(keyword in column_name.lower() for keyword in ['rating', 'рейтинг']):
                            worksheet.set_column(col_num, col_num, 10, rating_format)
                        elif any(keyword in column_name.lower() for keyword in ['date', 'время', 'timestamp']):
                            worksheet.set_column(col_num, col_num, 20, date_format)
                
                # Замораживаем первую строку
                worksheet.freeze_panes(1, 0)
            
            # Добавление графиков в Excel
            if len(df) > 1 and 'price' in df.columns:
                chart = workbook.add_chart({'type': 'column'})
                chart.add_series({
                    'name': 'Цены',
                    'categories': f"='Все товары'!$A$2:$A${len(df)+1}",
                    'values': f"='Все товары'!$D$2:$D${len(df)+1}",
                })
                chart.set_title({'name': 'Распределение цен'})
                writer.sheets['Все товары'].insert_chart('M2', chart)
        
        output.seek(0)
        return output
    
    @staticmethod
    def _create_wide_format_sheet(writer, df: pd.DataFrame):
        """Создание листа с характеристиками в широком формате"""
        # Выбираем только характеристики (не служебные поля)
        char_columns = [col for col in df.columns 
                       if col not in ['product_id', 'id', 'article', 'name', 'brand', 'price', 'salePrice', 'sale_price',
                                     'rating', 'feedbacks', 'url', 'timestamp', 'error', 
                                     'colors', 'sizes', 'stock_details', 'seller_feedbacks',
                                     'delivery_info', 'compositions', 'certificates', 'recent_feedbacks']]
        
        if char_columns:
            # Создаем новый DataFrame с характеристиками
            char_df = df[['product_id', 'name'] + char_columns]
            char_df.to_excel(writer, sheet_name='Характеристики', index=False)
    
    @staticmethod
    def _create_stats_sheet(writer, df: pd.DataFrame):
        """Создание листа со статистикой"""
        if df.empty:
            return
        
        stats_data = []
        
        # Основная статистика
        stats_data.append(['Общая статистика', ''])
        stats_data.append(['Всего товаров', len(df)])
        
        if 'brand' in df.columns:
            stats_data.append(['Уникальных брендов', df['brand'].nunique()])
        
        price_col = 'salePrice' if 'salePrice' in df.columns else 'price' if 'price' in df.columns else 'sale_price'
        if price_col in df.columns:
            stats_data.append(['Средняя цена', f"{df[price_col].mean():.2f} ₽"])
            stats_data.append(['Медианная цена', f"{df[price_col].median():.2f} ₽"])
            stats_data.append(['Минимальная цена', f"{df[price_col].min():.2f} ₽"])
            stats_data.append(['Максимальная цена', f"{df[price_col].max():.2f} ₽"])
        
        if 'rating' in df.columns:
            stats_data.append(['Средний рейтинг', f"{df['rating'].mean():.2f}"])
        
        if 'feedbacks' in df.columns:
            stats_data.append(['Всего отзывов', f"{df['feedbacks'].sum():,}"])
        
        if 'sale' in df.columns:
            stats_data.append(['Товаров со скидкой', (df['sale'] > 0).sum()])
            stats_data.append(['Процент товаров со скидкой', f"{(df['sale'] > 0).sum() / len(df) * 100:.1f}%"])
        
        # Статистика по брендам
        if 'brand' in df.columns:
            stats_data.append(['', ''])
            stats_data.append(['Топ брендов по количеству', ''])
            brand_counts = df['brand'].value_counts().head(10)
            for brand, count in brand_counts.items():
                stats_data.append([brand, count])
        
        # Создаем DataFrame
        stats_df = pd.DataFrame(stats_data, columns=['Параметр', 'Значение'])
        stats_df.to_excel(writer, sheet_name='Статистика', index=False)
    
    @staticmethod
    def _create_summary_sheet(writer, df: pd.DataFrame):
        """Создание листа с выжимкой"""
        summary_cols = ['product_id', 'id', 'article', 'name', 'brand', 'price', 'salePrice', 'sale_price',
                       'rating', 'feedbacks', 'all_characteristics']
        
        available_cols = [col for col in summary_cols if col in df.columns]
        
        if available_cols:
            summary_df = df[available_cols].copy()
            summary_df.to_excel(writer, sheet_name='Выжимка', index=False)

# ==================== ОСНОВНОЙ ИНТЕРФЕЙС ====================

def main():
    st.set_page_config(
        page_title="Продвинутый парсинг Wildberries",
        layout="wide",
        initial_sidebar_state="expanded"
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
    </style>
    """, unsafe_allow_html=True)
    
    st.title("🛍️ Продвинутый парсинг Wildberries")
    st.markdown("---")
    
    # Инициализация клиента
    if 'wb_client' not in st.session_state:
        st.session_state.wb_client = WBApiClient()
    
    if 'parsed_data' not in st.session_state:
        st.session_state.parsed_data = pd.DataFrame()
    
    # Боковая панель
    with st.sidebar:
        st.header("⚙️ Настройки парсинга")
        
        search_mode = st.radio(
            "Режим работы",
            ["Поиск по запросу", "Парсинг продавца", "По артикулам"],
            help="Выберите режим в зависимости от задачи"
        )
        
        st.subheader("📝 Параметры поиска")
        
        if search_mode == "Поиск по запросу":
            query = st.text_input("Поисковый запрос", value="ноутбук")
            pages = st.slider("Количество страниц", 1, 20, 3)
            products_per_page = st.select_slider(
                "Товаров на странице",
                options=[50, 100, 200, 300],
                value=100
            )
            
        elif search_mode == "Парсинг продавца":
            seller_id = st.text_input(
                "ID продавца",
                placeholder="Например: 123456",
                help="ID продавца можно найти в URL товара (параметр supplier)"
            )
            max_products = st.slider(
                "Максимальное количество товаров",
                10, 2000, 200
            )
            
        else:  # По артикулам
            articles_input = st.text_area(
                "Введите артикулы (через запятую или каждый с новой строки)",
                placeholder="12345678, 87654321\n98765432"
            )
            articles = [art.strip() for art in articles_input.replace('\n', ',').split(',') if art.strip()] if articles_input else []
        
        st.subheader("🔧 Дополнительные настройки")
        
        parse_details = st.checkbox("Собирать детальную информацию", value=True)
        full_characteristics = st.checkbox(
            "Полный парсинг характеристик", 
            value=True,
            help="Собирает ВСЕ характеристики из карточки товара"
        )
        
        use_selenium = st.checkbox("Использовать Selenium при необходимости", value=False)
        use_proxies = st.checkbox("Использовать прокси для обхода", value=True)
        
        delay_range = st.slider(
            "Задержка между запросами (сек)",
            0.5, 5.0, (1.0, 2.0)
        )
        
        if use_proxies:
            st.info("Используется ротация прокси для обхода защиты")
        
        st.subheader("💾 Настройки экспорта")
        export_format = st.selectbox("Формат экспорта", ["Excel", "CSV", "JSON"])
        include_stats = st.checkbox("Включать статистику", value=True)
    
    # Основная область
    tab1, tab2, tab3, tab4 = st.tabs(["📊 Парсинг", "📈 Аналитика", "🔍 Детали", "💾 Экспорт"])
    
    with tab1:
        st.header("Запуск парсинга")
        
        col1, col2 = st.columns([3, 1])
        with col1:
            if search_mode == "Парсинг продавца" and seller_id:
                parse_button = st.button(
                    "🚀 Парсить товары продавца",
                    type="primary",
                    use_container_width=True
                )
            elif search_mode == "Поиск по запросу" and query:
                parse_button = st.button(
                    "🚀 Начать поиск",
                    type="primary",
                    use_container_width=True
                )
            elif search_mode == "По артикулам" and articles:
                parse_button = st.button(
                    f"🚀 Парсить {len(articles)} артикулов",
                    type="primary",
                    use_container_width=True
                )
            else:
                parse_button = st.button(
                    "🚀 Начать парсинг",
                    type="primary",
                    use_container_width=True,
                    disabled=True
                )
        
        with col2:
            if not st.session_state.parsed_data.empty:
                if st.button("🗑️ Очистить данные", use_container_width=True):
                    st.session_state.parsed_data = pd.DataFrame()
                    st.rerun()
        
        if parse_button:
            if search_mode == "Поиск по запросу" and query:
                with st.spinner(f"Поиск товаров по запросу '{query}'..."):
                    try:
                        all_results = []
                        
                        for page in range(1, pages + 1):
                            df_page = st.session_state.wb_client.search_products(
                                query=query,
                                page=page,
                                limit=products_per_page
                            )
                            
                            if not df_page.empty:
                                all_results.append(df_page)
                                st.info(f"Страница {page}: найдено {len(df_page)} товаров")
                            
                            time.sleep(random.uniform(*delay_range))
                        
                        if all_results:
                            df = pd.concat(all_results, ignore_index=True)
                            
                            if parse_details and not df.empty:
                                with st.spinner("Сбор детальной информации..."):
                                    detail_progress = st.progress(0)
                                    details_list = []
                                    
                                    for idx, product_id in enumerate(df['id'].astype(str).tolist()):
                                        details = st.session_state.wb_client.get_product_details(
                                            product_id, 
                                            full_characteristics=full_characteristics
                                        )
                                        details_list.append(json.dumps(details))
                                        
                                        if idx % 10 == 0:
                                            detail_progress.progress((idx + 1) / len(df))
                                        
                                        time.sleep(random.uniform(*delay_range))
                                    
                                    detail_progress.progress(1.0)
                                    df['details'] = details_list
                            
                            st.session_state.parsed_data = df
                            st.success(f"✅ Найдено {len(df)} товаров")
                        else:
                            st.warning("Не найдено товаров по данному запросу")
                    
                    except Exception as e:
                        st.error(f"Ошибка при парсинге: {str(e)}")
            
            elif search_mode == "Парсинг продавца" and seller_id:
                with st.spinner(f"Парсинг товаров продавца {seller_id}..."):
                    try:
                        df = st.session_state.wb_client.card_parser.parse_seller_cards(
                            seller_id=seller_id,
                            max_products=max_products
                        )
                        
                        if not df.empty:
                            st.session_state.parsed_data = df
                            st.success(f"""
                            ✅ Успешно собраны данные!
                            
                            **Статистика:**
                            - Товаров: {len(df)}
                            - Уникальных брендов: {df['brand'].nunique() if 'brand' in df.columns else 0}
                            - Характеристик собрано: {len(df.columns) - 10} различных параметров
                            """)
                        else:
                            st.error("Не удалось собрать данные. Проверьте ID продавца.")
                    
                    except Exception as e:
                        st.error(f"Ошибка при парсинге: {str(e)}")
            
            elif search_mode == "По артикулам" and articles:
                with st.spinner(f"Парсинг {len(articles)} артикулов..."):
                    try:
                        all_results = []
                        
                        for i, article in enumerate(articles):
                            # Очищаем артикул от лишних символов
                            clean_article = re.sub(r'\D', '', article)
                            if clean_article:
                                # Ищем товар по артикулу
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
                                        df_art['details'] = json.dumps(details)
                                    
                                    all_results.append(df_art)
                                    st.info(f"Артикул {clean_article}: найден")
                                else:
                                    st.warning(f"Артикул {clean_article}: не найден")
                            
                            time.sleep(random.uniform(*delay_range))
                        
                        if all_results:
                            st.session_state.parsed_data = pd.concat(all_results, ignore_index=True)
                            st.success(f"✅ Найдено {len(st.session_state.parsed_data)} товаров")
                        else:
                            st.warning("Не найдено ни одного товара по указанным артикулам")
                    
                    except Exception as e:
                        st.error(f"Ошибка при парсинге: {str(e)}")
    
    # Отображение данных
    if not st.session_state.parsed_data.empty:
        df = st.session_state.parsed_data
        
        with tab2:
            st.header("Аналитика данных")
            
            # Статистика
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Всего товаров", len(df))
            with col2:
                if 'price' in df.columns:
                    avg_price = df['price'].mean()
                    st.metric("Средняя цена", f"{avg_price:,.0f} ₽")
                elif 'sale_price' in df.columns:
                    avg_price = df['sale_price'].mean()
                    st.metric("Средняя цена", f"{avg_price:,.0f} ₽")
            with col3:
                if 'feedbacks' in df.columns:
                    total_feedbacks = df['feedbacks'].sum()
                    st.metric("Всего отзывов", f"{total_feedbacks:,}")
            with col4:
                if 'brand' in df.columns:
                    unique_brands = df['brand'].nunique()
                    st.metric("Уникальных брендов", unique_brands)
            
            # Визуализации
            if len(df) > 1:
                st.subheader("📊 Визуализации")
                
                viz_col1, viz_col2 = st.columns(2)
                
                with viz_col1:
                    price_col = 'salePrice' if 'salePrice' in df.columns else 'price' if 'price' in df.columns else 'sale_price'
                    if price_col in df.columns:
                        st.subheader("Распределение цен")
                        fig, ax = plt.subplots(figsize=(10, 6))
                        sns.histplot(df[price_col], bins=30, kde=True, ax=ax)
                        ax.set_xlabel("Цена, руб")
                        ax.set_ylabel("Количество товаров")
                        st.pyplot(fig)
                
                with viz_col2:
                    if 'brand' in df.columns:
                        st.subheader("Топ1- брендов")
                        top_brands = df['brand'].value_counts().head(10)
                        if not top_brands.empty:
                            fig, ax = plt.subplots(figsize=(10, 6))
                            sns.barplot(x=top_brands.values, y=top_brands.index, ax=ax)
                            ax.set_xlabel("Количество товаров")
                            plt.tight_layout()
                            st.pyplot(fig)
                
                # Дополнительные графики
                if 'rating' in df.columns and price_col in df.columns:
                    st.subheader("Соотношение цена/рейтинг")
                    fig, ax = plt.subplots(figsize=(12, 6))
                    scatter = ax.scatter(df['rating'], df[price_col], 
                                       c=df['feedbacks'] if 'feedbacks' in df.columns else 0, 
                                       cmap='viridis', alpha=0.6, s=50)
                    ax.set_xlabel("Рейтинг")
                    ax.set_ylabel("Цена, руб")
                    if 'feedbacks' in df.columns:
                        plt.colorbar(scatter, label='Количество отзывов')
                    st.pyplot(fig)
        
        with tab3:
            st.header("Детальная информация")
            
            # Поиск и фильтрация
            st.subheader("🔍 Фильтрация и поиск")
            
            filter_col1, filter_col2, filter_col3 = st.columns(3)
            
            with filter_col1:
                if 'brand' in df.columns:
                    brands = ['Все'] + df['brand'].unique().tolist()
                    selected_brand = st.selectbox("Бренд", brands, key="brand_filter")
            
            with filter_col2:
                price_col = 'salePrice' if 'salePrice' in df.columns else 'price' if 'price' in df.columns else 'sale_price'
                if price_col in df.columns:
                    price_range = st.slider(
                        "Диапазон цен",
                        float(df[price_col].min()),
                        float(df[price_col].max()),
                        (float(df[price_col].min()), float(df[price_col].max())),
                        key="price_filter"
                    )
            
            with filter_col3:
                if 'rating' in df.columns:
                    rating_filter = st.slider(
                        "Минимальный рейтинг",
                        0.0, 5.0, 0.0, 0.1,
                        key="rating_filter"
                    )
            
            # Применяем фильтры
            filtered_df = df.copy()
            
            if 'brand' in df.columns and selected_brand != 'Все':
                filtered_df = filtered_df[filtered_df['brand'] == selected_brand]
            
            if price_col in filtered_df.columns:
                filtered_df = filtered_df[
                    (filtered_df[price_col] >= price_range[0]) & 
                    (filtered_df[price_col] <= price_range[1])
                ]
            
            if 'rating' in filtered_df.columns:
                filtered_df = filtered_df[filtered_df['rating'] >= rating_filter]
            
            st.info(f"Найдено товаров после фильтрации: {len(filtered_df)}")
            
            # Таблица с данными
            st.subheader("📋 Таблица данных")
            
            # Выбор колонок для отображения
            default_cols = ['product_id', 'name', 'brand', 'price', 'rating', 'feedbacks']
            
            all_columns = filtered_df.columns.tolist()
            selected_columns = st.multiselect(
                "Выберите колонки для отображения",
                all_columns,
                default=[col for col in default_cols if col in all_columns]
            )
            
            if selected_columns:
                st.dataframe(
                    filtered_df[selected_columns],
                    use_container_width=True,
                    height=400
                )
            
            # Детальный просмотр товара
            if not filtered_df.empty:
                st.subheader("🔍 Детальный просмотр товара")
                
                selected_idx = st.selectbox(
                    "Выберите товар для детального просмотра",
                    range(len(filtered_df)),
                    format_func=lambda x: f"{filtered_df.iloc[x]['name'][:100] if 'name' in filtered_df.columns else f'Товар {x}'}..."
                )
                
                product = filtered_df.iloc[selected_idx]
                
                col1, col2 = st.columns(2)
                
                with col1:
                    st.write("**Основная информация:**")
                    for key in ['product_id', 'id', 'article', 'name', 'brand', 'price', 'salePrice', 'sale_price', 
                               'rating', 'feedbacks', 'reviewRating', 'supplier']:
                        if key in product and pd.notna(product[key]):
                            st.write(f"**{key.replace('_', ' ').title()}:** {product[key]}")
                
                with col2:
                    st.write("**Дополнительно:**")
                    if 'url' in product:
                        st.markdown(f"[🔗 Открыть на Wildberries]({product['url']})")
                    
                    if 'all_characteristics' in product and product['all_characteristics']:
                        with st.expander("Показать все характеристики"):
                            st.write(product['all_characteristics'])
                    
                    if 'details' in product and product['details']:
                        with st.expander("Показать полные данные (JSON)"):
                            try:
                                details = json.loads(product['details'])
                                st.json(details)
                            except:
                                st.write(product['details'])
        
        with tab4:
            st.header("Экспорт данных")
            
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.metric("Готово к экспорту", len(st.session_state.parsed_data))
            
            with col2:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                default_name = f"wb_data_{timestamp}"
                
                if search_mode == "Парсинг продавца" and seller_id:
                    default_name = f"wb_seller_{seller_id}_{timestamp}"
                elif search_mode == "Поиск по запросу" and query:
                    default_name = f"wb_query_{query[:20]}_{timestamp}"
                
                file_name = st.text_input(
                    "Имя файла",
                    value=default_name
                )
            
            with col3:
                st.write("")
                st.write("")
            
            # Кнопки экспорта
            export_col1, export_col2, export_col3 = st.columns(3)
            
            with export_col1:
                if export_format == "Excel":
                    excel_data = EnhancedExcelExporter.export_to_excel(
                        df, 
                        f"{file_name}.xlsx",
                        include_stats=include_stats
                    )
                    
                    st.download_button(
                        label="📥 Скачать Excel",
                        data=excel_data,
                        file_name=f"{file_name}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True
                    )
            
            with export_col2:
                if export_format == "CSV":
                    csv_data = df.to_csv(index=False, encoding='utf-8-sig').encode()
                    
                    st.download_button(
                        label="📥 Скачать CSV",
                        data=csv_data,
                        file_name=f"{file_name}.csv",
                        mime="text/csv",
                        use_container_width=True
                    )
            
            with export_col3:
                if export_format == "JSON":
                    json_data = df.to_json(orient='records', force_ascii=False, indent=2).encode()
                    
                    st.download_button(
                        label="📥 Скачать JSON",
                        data=json_data,
                        file_name=f"{file_name}.json",
                        mime="application/json",
                        use_container_width=True
                    )
            
            # Предварительный просмотр
            st.subheader("Предварительный просмотр данных")
            preview_cols = ['product_id', 'name', 'brand', 'price', 'rating', 'feedbacks']
            available_preview_cols = [col for col in preview_cols if col in df.columns]
            
            if available_preview_cols:
                st.dataframe(
                    df[available_preview_cols].head(20),
                    use_container_width=True
                )
    else:
        # Инструкция
        with tab1:
            st.info("""
            ## 📖 Инструкция:
            
            ### 1. Выберите режим работы:
            - **Поиск по запросу**: Поиск товаров по ключевым словам
            - **Парсинг продавца**: Сбор ВСЕХ товаров конкретного продавца
            - **По артикулам**: Поиск конкретных товаров по артикулам
            
            ### 2. Настройте параметры:
            - Количество товаров или страниц
            - Задержку между запросами (рекомендуется 1-2 секунды)
            - Опцию "Полный парсинг характеристик" для сбора всех данных
            
            ### 3. Нажмите "Начать парсинг"
            
            ## 🔍 Что собирается:
            - ✅ Все основные данные товаров
            - ✅ **ВСЕ характеристики** из карточки (при включенной опции)
            - ✅ Информация о продавце
            - ✅ Остатки на складах
            - ✅ Отзывы и рейтинги
            - ✅ Информация о доставке
            - ✅ SEO данные
            
            ## ⚠️ Важно:
            - Не используйте слишком агрессивный парсинг
            - Соблюдайте задержки между запросами
            - Данные сохраняются в Excel с несколькими листами
            """)

if __name__ == "__main__":
    # Установка зависимостей:
    # pip install streamlit requests pandas matplotlib seaborn xlsxwriter 
    # pip install fake-useragent cloudscraper undetected-chromedriver fp.free-proxy
    # pip install aiohttp asyncio
    
    main()
