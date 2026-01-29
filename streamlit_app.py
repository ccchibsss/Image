import streamlit as st
import requests
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import time
import re
from collections import Counter

def extract_product_id_from_url(url):
    # Регулярное выражение для извлечения ID товара из URL
    match = re.search(r"wildberries\.ru/catalog/(\d+)", url)
    if match:
        return match.group(1)
    return None

def fetch_card_details(product_id, retries=3, delay=2):
    url = f"https://wbx-content-v2.wbstatic.net/ru/{product_id}.json"
    for attempt in range(retries):
        try:
            response = requests.get(url)
            if response.status_code == 200:
                data = response.json()
                characteristics = data.get("characteristics", [])
                applicability = data.get("applicability", [])
                characteristics_str = ", ".join([f"{c['name']}: {c['value']}" for c in characteristics]) if characteristics else ""
                applicability_str = ", ".join([a['name'] for a in applicability]) if applicability else ""
                all_data_str = str(data)
                return characteristics_str, applicability_str, all_data_str
            elif response.status_code == 429:
                st.warning("Превышен лимит запросов, повтор через 10 секунд")
                time.sleep(10)
            else:
                return "", "", ""
        except Exception as e:
            st.error(f"Ошибка при получении данных для ID {product_id}: {e}")
            time.sleep(delay)
    return "", "", ""

def fetch_wb_data(query, page=1):
    url = (
        f"https://search.wb.ru/exactmatch/ru/common/v18/search"
        f"?appType=1&curr=rub&dest=-1257786&lang=ru&page={page}"
        f"&query={requests.utils.quote(query)}&resultset=catalog&sort=popular&spp=30"
    )
    # Для отладки выводим URL
    st.write(f"URL запроса: {url}")

    try:
        response = requests.get(url)
    except requests.exceptions.RequestException as e:
        st.error(f"Ошибка при выполнении запроса: {e}")
        return pd.DataFrame()

    if response.status_code != 200:
        st.error(f"Ошибка API: статус {response.status_code}")
        return pd.DataFrame()

    if not response.text:
        st.warning("Пустой ответ API")
        return pd.DataFrame()

    try:
        data = response.json()
    except ValueError:
        st.error("Некорректный JSON-ответ от API")
        return pd.DataFrame()

    products = data.get("products", [])
    items = []
    for p in products:
        size_info = p.get("sizes", [{}])[0]
        price = size_info.get("price", {}).get("product", 0) / 100 if size_info else 0
        item = {
            "id": p.get("id"),
            "name": p.get("name"),
            "brand": p.get("brand"),
            "price": price,
            "rating": p.get("rating", 0),
            "feedbacks": p.get("feedbacks", 0),
        }
        items.append(item)
    return pd.DataFrame(items)

def main():
    st.set_page_config(page_title="Аналитика Wildberries", layout="wide")
    st.title("Аналитика данных товаров Wildberries")
    
    with st.sidebar:
        st.header("Настройки поиска")
        search_type = st.radio("Выберите тип поиска", ("По запросу", "По ссылке"))
        if search_type == "По запросу":
            search_query = st.text_input("Введите запрос для поиска", value="смартфон")
        else:
            product_url = st.text_input("Введите ссылку на товар Wildberries")
        page_number = st.number_input("Номер страницы", min_value=1, max_value=10, value=1)

    # Обработка поиска
    if search_type == "По запросу" and search_query:
        with st.spinner("Загружаем данные по запросу..."):
            df = fetch_wb_data(search_query, page=page_number)
    elif search_type == "По ссылке" and product_url:
        product_id = extract_product_id_from_url(product_url)
        if product_id:
            with st.spinner("Загружаем данные по ссылке..."):
                characteristics, applicability, full_data = fetch_card_details(product_id)
                # Создаем DataFrame с этим товаром
                df = pd.DataFrame([{
                    "id": product_id,
                    "name": "Товар из ссылки",
                    "brand": "Бренд по данным",
                    "price": None,
                    "rating": None,
                    "feedbacks": None,
                    "characteristics": characteristics,
                    "applicability": applicability,
                    "all_card_data": full_data
                }])
        else:
            st.error("Не удалось извлечь ID товара из ссылки.")
            df = pd.DataFrame()
    else:
        df = pd.DataFrame()

    # Если есть данные
    if not df.empty:
        # Получение характеристик и применимости для товаров из поиска
        st.info("Получение характеристик и применимости карточек...")
        characteristics_list = []
        applicability_list = []
        all_card_data_list = []

        for idx, row in df.iterrows():
            if row['id']:
                charac, app, all_data = fetch_card_details(row['id'])
                characteristics_list.append(charac)
                applicability_list.append(app)
                all_card_data_list.append(all_data)
                time.sleep(0.5)
            else:
                characteristics_list.append('')
                applicability_list.append('')
                all_card_data_list.append('')

        df['characteristics'] = characteristics_list
        df['applicability'] = applicability_list
        df['all_card_data'] = all_card_data_list

        # Фильтр по цене и рейтингу
        min_price = st.sidebar.number_input("Мин. цена", 0.0, 1000000.0, 0.0)
        max_price = st.sidebar.number_input("Макс. цена", 0.0, 1000000.0, 30000.0)
        min_rating = st.sidebar.slider("Мин. рейтинг", 0.0, 5.0, 0.0, 0.5)

        df_filtered = df[
            (df['price'] >= min_price) &
            (df['price'] <= max_price) &
            (df['rating'] >= min_rating)
        ]

        # Анализ характеристик
        st.info("Анализ характеристик товаров...")
        characteristics_expanded = []
        for idx, row in df_filtered.iterrows():
            try:
                characteristics_expanded.append(row['characteristics'])
            except:
                characteristics_expanded.append('')
        all_chars = []
        for ch_str in characteristics_expanded:
            if ch_str:
                all_chars.extend([c.strip() for c in ch_str.split(',')])
        if all_chars:
            top_chars = Counter(all_chars).most_common(10)
            # Визуализация топ характеристик
            st.subheader("ТОП 10 характеристик товаров")
            fig_char, ax_char = plt.subplots()
            sns.barplot(x=[tc[1] for tc in top_chars], y=[tc[0] for tc in top_chars], ax=ax_char)
            ax_char.set_xlabel("Количество товаров")
            st.pyplot(fig_char)

            # Выбор характеристики для анализа
            selected_char = st.selectbox("Выберите характеристику для анализа", options=[tc[0] for tc in top_chars])
            # Фильтр товаров по выбранной характеристике
            filtered_indices = []
            for idx, ch_str in enumerate(characteristics_expanded):
                if selected_char in ch_str:
                    filtered_indices.append(df_filtered.index[idx])
            df_char_filtered = df_filtered.loc[filtered_indices]
        else:
            df_char_filtered = df_filtered

        # Основные фильтры
        df_final = df_char_filtered[
            (df_char_filtered['price'] >= min_price) &
            (df_char_filtered['price'] <= max_price) &
            (df_char_filtered['rating'] >= min_rating)
        ]

        if df_final.empty:
            st.warning("Нет товаров после фильтрации.")
        else:
            st.success(f"Товары после фильтрации: {len(df_final)}")
            # Таблица
            st.subheader("Отфильтрованные товары")
            st.dataframe(df_final)

            # Скачать CSV
            csv = df_final.to_csv(index=False).encode()
            st.download_button("Скачать CSV", data=csv, file_name="wb_filtered.csv", mime="text/csv")

            # Графики
            st.subheader("Распределение цен")
            fig1, ax1 = plt.subplots()
            sns.histplot(df_final['price'], bins=20, kde=True, ax=ax1)
            ax1.set_xlabel("Цена, руб")
            st.pyplot(fig1)

            st.subheader("Топ-10 брендов по количеству товаров")
            top_brands = df_final['brand'].value_counts().head(10)
            fig2, ax2 = plt.subplots()
            sns.barplot(x=top_brands.values, y=top_brands.index, ax=ax2)
            ax2.set_xlabel("Количество товаров")
            st.pyplot(fig2)

            st.subheader("Топ-10 брендов по средней цене")
            brand_avg = df_final.groupby('brand')['price'].mean().sort_values(ascending=False).head(10)
            fig3, ax3 = plt.subplots()
            sns.barplot(x=brand_avg.values, y=brand_avg.index, ax=ax3)
            ax3.set_xlabel("Средняя цена")
            st.pyplot(fig3)

            # Детали выбранного товара
            if not df_final.empty:
                selected_idx = st.number_input(
                    "Введите индекс товара для просмотра деталей (от 0 до {})".format(len(df_final)-1),
                    min_value=0,
                    max_value=len(df_final)-1,
                    value=0
                )
                selected_product = df_final.iloc[selected_idx]
                st.subheader("Детали товара")
                st.write("**Наименование:**", selected_product['name'])
                st.write("**Бренд:**", selected_product['brand'])
                st.write("**Цена:**", selected_product['price'])
                st.write("**Рейтинг:**", selected_product['rating'])
                st.write("**Характеристики:**", selected_product['characteristics'])
                st.write("**Применимость:**", selected_product['applicability'])
                st.write("**Полные данные карточки:**", selected_product['all_card_data'])
    else:
        st.info("Введите поисковый запрос или ссылку на товар для начала работы.")

if __name__ == "__main__":
    main()
