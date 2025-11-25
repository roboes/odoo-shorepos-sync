## Shore POS Tests
# Last update: 2025-11-25

# Token is valid for 10 hours

# Import packages
from datetime import datetime, timedelta
import os

import numpy as np
import pandas as pd
import requests
from requests.exceptions import HTTPError
import time

# Settings
settings_shorepos_api_endpoint_url = 'https://app.inventorum.com/api'
settings_shorepos_client_id = ''
settings_shorepos_client_secret = ''
settings_shorepos_refresh_token = ''
settings_shorepos_access_token = ''
settings_shorepos_timeout = 30


def shorepos_token_get():
    """Retrieves Shore POS access token and new refresh token."""

    if not settings_shorepos_api_endpoint_url or not settings_shorepos_client_id or not settings_shorepos_client_secret or not settings_shorepos_refresh_token or not settings_shorepos_timeout:
        print('Missing Shore POS API configuration details (url, client id, client secret, refresh token or timeout). Cannot retrieve refresh token.')
        return False

    try:
        response = requests.post(
            url=f'{settings_shorepos_api_endpoint_url}/auth/token/',
            auth=(settings_shorepos_client_id, settings_shorepos_client_secret),
            data={'grant_type': 'refresh_token', 'refresh_token': settings_shorepos_refresh_token},
            timeout=settings_shorepos_timeout,
        )
        response.raise_for_status()
        response_data = response.json()

    except requests.RequestException as error:
        print('Shore POS token retrieval error: %s', error)
        return False

    shorepos_access_token = response_data.get('access_token')
    shorepos_refresh_token_new_expiry_date = response_data.get('expires_in')

    # Check if we received a valid access token
    if not shorepos_access_token:
        return False

    if shorepos_access_token:
        print(datetime.now() + timedelta(seconds=shorepos_refresh_token_new_expiry_date))
        return shorepos_access_token


def shorepos_api_request(method, endpoint, api_version=None, params=None, data=None, json=None, files=None, timeout=30):
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {settings_shorepos_access_token}',
        'X-Api-Version': api_version or '13',
    }
    if (json is not None or data is not None) and not files:
        headers['Content-Type'] = 'application/json'

    response = requests.request(method=method, url=f'{settings_shorepos_api_endpoint_url}/{endpoint}/', headers=headers, params=params, data=data, json=json, files=files, timeout=timeout)
    response.raise_for_status()
    return response.json()


def shorepos_api_request_all(method, endpoint, api_version=None, params=None, data=None, json=None, files=None, timeout=30):
    items_all = []
    page = 1
    total_pages = None

    if params is None:
        params = {}
    params.setdefault('limit', 100)

    while True:
        try:
            params['page'] = page

            page_info = f' of {total_pages}' if total_pages else ''
            print(f'Fetching page {page}{page_info}...')

            response = shorepos_api_request(method=method, endpoint=endpoint, api_version=api_version, params=params, data=data, json=json, files=files, timeout=timeout)

            if not response:
                break

            if isinstance(response, list):
                items_all.extend(response)

                print(f'✅ Finished. Total items collected: {len(items_all)}')

                break

            if isinstance(response, dict):
                if page == 1:
                    total_count = response.get('total')
                    if total_count is not None and isinstance(total_count, int):
                        # Calculate total pages using ceiling division
                        total_pages = (total_count + params['limit'] - 1) // params['limit']
                        print(f'📚 Total records found: {total_count}. Total pages to fetch: {total_pages}.')

                items = None

                if 'results' in response and isinstance(response['results'], list):
                    items = response['results']

                elif 'data' in response:
                    items = response['data']

                if items is not None:
                    items_all.extend(items)

                    print(f'Page {page} fetched {len(items)} items. Total collected: {len(items_all)}')

                    if len(items) < params['limit']:
                        print(f'✅ Finished. Detected final page with {len(items)} items. Total items collected: {len(items_all)}')
                        break

                    page += 1
                    time.sleep(0.05)
                    continue

        except HTTPError as error:
            if error.response.status_code == 429:
                retry_after = int(error.response.headers.get('Retry-After', 1))
                print(f'⚠️ Rate limit hit (HTTP 429), retrying after {retry_after}s...')
                time.sleep(retry_after)
                continue
            else:
                raise

    return items_all


settings_shorepos_access_token = shorepos_token_get()

# Products
# shorepos_products = shorepos_api_request(method='get', endpoint='products', params={'limit': 100})
# shorepos_products = shorepos_products['results']

# shorepos_products = shorepos_api_request(method='get', endpoint='products/12345678')
# shorepos_products = shorepos_api_request(method='get', endpoint='products/delta/modified', params={'limit': 100, 'start_date': f'{(datetime.now(timezone.utc) - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z', 'verbose': True}) # Endpoint has a bug with the pagination on API Version 13

shorepos_products = shorepos_api_request_all(method='get', endpoint='products')
shorepos_product = next((item for item in shorepos_products if item['id'] == 12345678), None)

shorepos_taxes = shorepos_api_request_all(method='get', endpoint='taxes')
shorepos_categories = shorepos_api_request_all(method='get', endpoint='categories')

tax_lookup = {tax['id']: tax['name'] for tax in shorepos_taxes}
category_lookup = {category['id']: category['name'] for category in shorepos_categories}

for product in shorepos_products:
    # Replace tax_type ID with the corresponding name
    tax_id = product.get('tax_type')
    if tax_id and tax_id in tax_lookup:
        product['tax_type'] = tax_lookup[tax_id]

    # Replace category IDs with their names (if a product has multiple categories, only the first category is assigned)
    category_ids = product.get('categories', [])
    if category_ids:
        product['categories'] = category_lookup.get(category_ids[0])
    else:
        product['categories'] = None


shorepos_products_df = pd.DataFrame(data=shorepos_products, index=None, dtype='str')

with pd.ExcelWriter(
    path=os.path.join(os.path.expanduser('~'), 'Downloads', 'Shore POS Products.xlsx'),
    date_format='YYYY-MM-DD',
    datetime_format='YYYY-MM-DD HH:MM:SS',
    engine='xlsxwriter',
    engine_kwargs={'options': {'strings_to_formulas': False, 'strings_to_urls': False}},
) as writer:
    shorepos_products_df.to_excel(excel_writer=writer, sheet_name='Shore POS Products', na_rep='', header=True, index=False, index_label=None, freeze_panes=(1, 0))


# Orders
shorepos_orders = shorepos_api_request_all(method='get', endpoint='orders', params={'start': '2022.01.01 00:00:00', 'end': '2025.10.31 23:59:59', 'channel': 'ipad', 'limit': 40})

shorepos_orders_df = (
    pd.json_normalize(
        data=shorepos_orders,
        record_path=['basket', 'items'],
        meta=[['id'], ['state'], ['basket', 'invoice_number'], ['amount_paid'], ['channel'], ['completed_at'], ['basket', 'receipt', 'pdf']],
        sep='.',
        meta_prefix='order_',
        max_level=None,
        errors='ignore',
    )
    .rename(columns={'order_basket.invoice_number': 'invoice_number'})
    .astype(dtype={'original_quantity': 'float'})
    .assign(order_completed_at=lambda row: pd.to_datetime(arg=row['order_completed_at'], utc=False, format=None))
)


# Map product IDs to their primary category (if a product has multiple categories, only the first category is assigned)
product_lookup = {product['id']: product.get('categories') for product in shorepos_products}
shorepos_orders_df['category'] = shorepos_orders_df['product'].map(product_lookup)

# Assign human-readable order state names based on Shore POS bitmask - # https://api-docs.inventorum.com/api/orders.html#order-states
order_state_mapping = {
    1: {'name': 'Draft', 'description': 'All is possible with this state.'},
    2: {'name': 'Pending', 'description': 'Temporarily frozen, waiting for money.'},
    4: {'name': 'Canceled', 'description': 'End of processing, nothing will happen, no responsibilities.'},
    8: {'name': 'Closed', 'description': 'Frozen, indicator for is_paid, can only be shipped or delivered or refunded.'},
    16: {'name': 'Refunded', 'description': 'End of processing, has been returned.'},
    32: {'name': 'Shipped', 'description': 'Frozen, can only be delivered or refunded'},
    64: {'name': 'Delivered', 'description': 'Frozen, can only be refunded. end of processing after return time.'},
    128: {'name': 'Test_print', 'description': '-'},
    256: {'name': 'Paid', 'description': '-'},
    512: {'name': 'Read', 'description': '-'},
}

shorepos_orders_df['order_state'] = shorepos_orders_df['order_state'].apply(
    lambda order_state_value: ', '.join([state_info['name'] for state_flag, state_info in order_state_mapping.items() if order_state_value & state_flag])
)

shorepos_orders_df = shorepos_orders_df.assign(order_state=lambda row: row['order_state'].replace(to_replace=r'Closed, Shipped, Delivered, Paid', value=r'Completed', regex=False))

# List all unique order states currently in the dataset
print(sorted(shorepos_orders_df['order_state'].dropna().unique()))


shorepos_orders_df = (
    shorepos_orders_df.filter(items=['order_completed_at', 'invoice_number', 'type', 'order_state', 'category', 'product', 'product_code', 'name', 'original_quantity', 'tax_rate', 'gross_price'])
    .sort_values(by=['order_completed_at', 'invoice_number'], ignore_index=True)
    .assign(original_quantity=lambda row: np.where(row['order_state'].str.contains('Canceled', na=False), -row['original_quantity'], row['original_quantity']))
)

# Delete objects
# del shorepos_categories, shorepos_products, shorepos_taxes
