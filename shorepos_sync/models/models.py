from base64 import b64decode
from datetime import datetime, timedelta, UTC
from decimal import Decimal
from io import BytesIO
import logging
import requests
from requests.exceptions import HTTPError
import time
from types import SimpleNamespace
from typing import Any

import cv2
import filetype
import numpy as np

from odoo import _, api, fields, models
from odoo.addons.queue_job.delay import chain
from odoo.exceptions import UserError
from odoo.release import version_info


# Settings
_logger = logging.getLogger(__name__)


class ShoreposConnector(models.Model):
    _name = 'shorepos.configuration'
    _description = 'Shore POS Configuration'

    # View settings
    shorepos_connection_sequence = fields.Char(string='Connection ID', required=True, copy=False, readonly=True, index=True, default=lambda self: _('New'))

    # Shore POS API settings
    settings_shorepos_connection_name = fields.Char(string='Instance Name')
    settings_shorepos_store_identifier = fields.Char(
        string='Shore POS Store Identifier',
        help="A unique identifier used to link records to a Shore POS store. This can be a full URL, domain, or short store slug depending on your setup. Examples: 'https://mystore.com', 'my-store-123'.",
    )
    settings_shorepos_api_endpoint_url = fields.Char(string='API Endpoint URL', default='https://app.inventorum.com/api')
    settings_shorepos_client_id = fields.Char(string='Client ID')
    settings_shorepos_client_secret = fields.Char(string='Client Secret')
    settings_shorepos_access_token = fields.Char(string='Access Token', readonly=True)
    settings_shorepos_refresh_token = fields.Char(string='Refresh Token')
    settings_shorepos_token_expiry_date = fields.Datetime(string='Token Expiry', readonly=True)
    settings_shorepos_timeout = fields.Integer(string='Timeout', default=30)

    # Sync items settings
    settings_odoo_to_shorepos_products_sync = fields.Boolean(default=True)
    settings_odoo_to_shorepos_product_variations_sync = fields.Boolean(default=True, readonly=True)

    # General settings
    settings_shorepos_images_sync = fields.Boolean(string='Sync images?', default=True)

    # Stock management
    settings_shorepos_products_stock_management = fields.Boolean(string='Sync stock quantity?', default=True)
    settings_shorepos_products_warehouse_location = fields.Many2one(
        comodel_name='stock.warehouse', string='Warehouse', help='Warehouse for syncing Shore POS products stock quantity.', default=lambda self: self.env.ref('stock.warehouse0'), ondelete='set null'
    )

    # Odoo to Shore POS products import settings
    settings_shorepos_odoo_to_shorepos_products_language_code = fields.Char(
        string="Filter Odoo products by language defined in the 'product_language_code' field",
        help="2-digit language code (ISO 639-1) (e.g. 'en').",
    )
    settings_shorepos_products_package_size_unit_default = fields.Selection(
        selection=[('odoo', 'Retrieve from Odoo'), ('ml', 'ml'), ('l', 'l'), ('g', 'g'), ('kg', 'kg'), ('m', 'm'), ('m2', 'm2'), ('m3', 'm3'), ('pc', 'piece')],
        string='Default Shore POS package size',
        help='Default Shore POS package size when syncing an Odoo product to Shore POS.',
        default='pc',
    )

    # Scheduled sync settings
    settings_shorepos_sync_scheduled = fields.Boolean('Enable auto-sync')
    settings_shorepos_sync_scheduled_interval_minutes = fields.Integer(string='Interval (in Minutes)', default=5)
    ir_cron_id = fields.Many2one(comodel_name='ir.cron', string='Scheduled Cron Job', ondelete='cascade')

    # Last synced
    odoo_shorepos_last_sync = fields.Datetime(string='Last Synced', compute='odoo_shorepos_last_sync_assign', store=False, readonly=True)

    def odoo_shorepos_last_sync_assign(self: models.Model) -> None:
        self.ensure_one()
        sync_log = self.env['shorepos.sync.log'].search([], limit=1)
        self.odoo_shorepos_last_sync = sync_log.odoo_shorepos_last_sync if sync_log else False

    @api.model_create_multi
    def create(self: models.Model, values_list: list[dict[str, Any]]) -> models.Model:
        for values in values_list:
            if values.get('shorepos_connection_sequence', _('New')) == _('New'):
                values['shorepos_connection_sequence'] = self.env['ir.sequence'].next_by_code('shorepos.configuration.sequence') or _('New')

        records = super().create(values_list)

        # Run post-creation logic per record
        for record in records:
            record.shorepos_token_get()
            record.cron_job_update()

        return records

    def write(self: models.Model, values: dict[str, Any]) -> bool:
        # Skip cron update if called from cron context
        if self.env.context.get('ir_cron'):
            return super().write(values)

        if self.env.context.get('skip_token_refresh_write'):
            return super().write(values)

        success = super().write(values)

        for record in self:
            record.shorepos_token_get()
            record.cron_job_update()

        return success

    def unlink(self: models.Model) -> bool:
        """Deletes associated cron jobs when a configuration record is deleted."""
        for record in self:
            if record.ir_cron_id:
                record.ir_cron_id.unlink()
        return super().unlink()

    def cron_job_update(self: models.Model) -> None:
        self.ensure_one()

        if version_info[0] == 16:
            cron_values = {
                'name': f'Shore POS Auto-Sync - {self.settings_shorepos_store_identifier}',
                'model_id': self.env['ir.model']._get(self._name).id,
                'code': (
                    f'model.with_context(cron_running=True).browse({self.id}).with_delay().shorepos_sync()'
                    if self.env['ir.module.module'].search([('name', '=', 'queue_job'), ('state', '=', 'installed')], limit=1)
                    else f'model.with_context(cron_running=True).browse({self.id}).shorepos_sync()'
                ),
                'active': self.settings_shorepos_sync_scheduled,
                'interval_number': self.settings_shorepos_sync_scheduled_interval_minutes,
                'interval_type': 'minutes',
                'numbercall': -1,
                'doall': True,
            }

        elif version_info[0] == 18:
            cron_values = {
                'name': f'Shore POS Auto-Sync - {self.settings_shorepos_store_identifier}',
                'model_id': self.env['ir.model']._get(self._name).id,
                'code': (
                    f'model.with_context(cron_running=True).browse({self.id}).with_delay().shorepos_sync()'
                    if self.env['ir.module.module'].search([('name', '=', 'queue_job'), ('state', '=', 'installed')], limit=1)
                    else f'model.with_context(cron_running=True).browse({self.id}).shorepos_sync()'
                ),
                'active': self.settings_shorepos_sync_scheduled,
                'interval_number': self.settings_shorepos_sync_scheduled_interval_minutes,
                'interval_type': 'minutes',
            }

        # Update the existing cron job
        if self.ir_cron_id:
            self.ir_cron_id.write(cron_values)
        # Create only if scheduled to avoid unnecessary cron jobs
        elif self.settings_shorepos_sync_scheduled:
            self.ir_cron_id = self.env['ir.cron'].create(cron_values)

    def shorepos_sync_action(self: models.Model) -> dict[str, Any]:
        self.ensure_one()
        _logger.info("Manual 'Sync Now' button pressed, triggering background sync.")

        # Run shorepos_sync in the background (requires 'queue_job' add-on)
        if self.env['ir.module.module'].search([('name', '=', 'queue_job'), ('state', '=', 'installed')], limit=1):
            self.with_delay().shorepos_sync()

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sync Started (Queue Job)'),
                    'message': _('Shore POS sync process has been started in the background. %s'),
                    'links': [{'label': _('Open Job Queue'), 'url': '/web#action=%d&model=queue.job&view_type=list' % self.env['ir.actions.act_window'].search([('res_model', '=', 'queue.job')], limit=1).id}],
                    'sticky': False,
                },
            }

        else:
            self.shorepos_sync()

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sync Started (Synchronous)'),
                    'message': _('Shore POS sync process has been started and is running synchronously.'),
                    'sticky': False,
                },
            }

    def shorepos_sync(self: models.Model) -> None:
        self.ensure_one()

        # Shore POS access token
        if not self.settings_shorepos_token_expiry_date or fields.Datetime.now() >= self.settings_shorepos_token_expiry_date:
            return self.shorepos_token_get()

        queue_jobs_run_in_sequence = []

        # Odoo to Shore POS

        ## Products
        if self.settings_odoo_to_shorepos_products_sync:
            ### Products delete
            queue_jobs_run_in_sequence.append(self.delayable(priority=None, description=None).odoo_to_shorepos_products_delete())

            ### Products and products variants
            queue_jobs_run_in_sequence.append(self.delayable(priority=None, description=None).odoo_to_shorepos_products_sync())

        # Stock quantity
        if self.settings_shorepos_products_stock_management:
            queue_jobs_run_in_sequence.append(self.delayable(priority=None, description=None).odoo_shorepos_products_stock_quantity_sync_batch())
            queue_jobs_run_in_sequence.append(self.delayable(priority=None, description=None).update_sync_last_log(model_name='shorepos.stock.sync.log', field_name='odoo_shorepos_last_sync'))

        # Store 'odoo_shorepos_last_sync'
        queue_jobs_run_in_sequence.append(self.delayable(priority=None, description=None).update_sync_last_log(model_name='shorepos.sync.log', field_name='odoo_shorepos_last_sync'))

        # Create chain and delay the jobs
        if queue_jobs_run_in_sequence:
            chain(*queue_jobs_run_in_sequence).delay()

    @api.model
    def update_sync_last_log(self: models.Model, model_name: str, field_name: str) -> None:
        sync_log = self.env[model_name].search([], limit=1)

        if sync_log:
            sync_log.write({field_name: fields.Datetime.now()})

        else:
            self.env[model_name].create({field_name: fields.Datetime.now()})

    def shorepos_token_get(self: models.Model) -> bool | None:
        """Retrieves Shore POS access token and new refresh token."""
        self.ensure_one()

        if (
            not self.settings_shorepos_store_identifier
            or not self.settings_shorepos_api_endpoint_url
            or not self.settings_shorepos_client_id
            or not self.settings_shorepos_client_secret
            or not self.settings_shorepos_refresh_token
            or not self.settings_shorepos_timeout
        ):
            _logger.error('Missing Shore POS API configuration details (url, client id, client secret, refresh token or timeout). Cannot retrieve refresh token')
            return False

        try:
            response = requests.post(
                url=f'{self.settings_shorepos_api_endpoint_url}/auth/token/',
                auth=(self.settings_shorepos_client_id, self.settings_shorepos_client_secret),
                data={'grant_type': 'refresh_token', 'refresh_token': self.settings_shorepos_refresh_token},
                timeout=self.settings_shorepos_timeout,
            )
            response.raise_for_status()
            response_data = response.json()

        except requests.RequestException as error:
            error_message = f'Shore POS token retrieval error: {error}'
            _logger.error(error_message)
            raise UserError(_(error_message))

        api_data = {}

        shorepos_access_token = response_data.get('access_token')
        shorepos_refresh_token_new = response_data.get('refresh_token')
        shorepos_refresh_token_new_expiry_date = response_data.get('expires_in')

        # Check if we received a valid access token
        if not shorepos_access_token:
            _logger.error(f'Failed to get Shore POS access token; Response: {response_data}')
            return False

        if shorepos_access_token:
            api_data['settings_shorepos_access_token'] = shorepos_access_token

        if shorepos_refresh_token_new and shorepos_refresh_token_new != self.settings_shorepos_refresh_token:
            api_data['settings_shorepos_refresh_token'] = shorepos_refresh_token_new

        if shorepos_refresh_token_new_expiry_date:
            api_data['settings_shorepos_token_expiry_date'] = fields.Datetime.now() + timedelta(seconds=shorepos_refresh_token_new_expiry_date)

        if api_data:
            self.with_context(skip_token_refresh_write=True).write(api_data)

        _logger.info('Shore POS API connection successful')

    def shorepos_api_request(
        self: models.Model,
        method: str,
        endpoint: str,
        api_version: str | None = None,
        params: dict[str, Any] | None = None,
        data: Any | None = None,
        json: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        timeout: int | float = 30,
    ) -> dict[str, Any]:
        self.ensure_one()

        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {self.settings_shorepos_access_token}',
            'X-Api-Version': api_version or '13',
        }
        if (json is not None or data is not None) and not files:
            headers['Content-Type'] = 'application/json'

        response = requests.request(method=method, url=f'{self.settings_shorepos_api_endpoint_url}/{endpoint}/', headers=headers, params=params, data=data, json=json, files=files, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def shorepos_api_request_all(
        self: models.Model,
        method: str,
        endpoint: str,
        api_version: str | None = None,
        params: dict[str, Any] | None = None,
        data: Any | None = None,
        json: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        timeout: int | float = 30,
    ) -> list[dict[str, Any]]:
        self.ensure_one()

        items_all = []
        page = 1

        # Shore POS parameters
        if params is None:
            params = {}
        params.setdefault('limit', 100)

        while True:
            try:
                params['page'] = page

                response = self.shorepos_api_request(method=method, endpoint=endpoint, api_version=api_version, params=params, data=data, json=json, files=files, timeout=timeout)

                if not response:
                    break

                if isinstance(response, list):
                    items_all.extend(response)
                    break

                if isinstance(response, dict):
                    items = None

                    if 'results' in response and isinstance(response['results'], list):
                        items = response['results']

                    elif 'data' in response:
                        items = response['data']

                    if items is not None:
                        items_all.extend(items)

                        if len(items) < params['limit']:
                            break

                        page += 1
                        time.sleep(0.05)
                        continue

            except HTTPError as error:
                if error.response.status_code == 429:
                    retry_after = int(error.response.headers.get('Retry-After', 1))
                    _logger.warning(f'Rate limit hit, retrying after {retry_after}s...')
                    time.sleep(retry_after)
                    continue
                else:
                    raise

        return items_all

    def shorepos_attributes_build(self: models.Model, odoo_product: models.Model) -> dict[str, list[str]]:
        """Builds the attributes dictionary for a simple product from Odoo's attribute lines."""

        self.ensure_one()

        attributes = {}

        for attribute_line in odoo_product.attribute_line_ids:
            # Get the attribute name (e.g. "color")
            attribute_name = attribute_line.attribute_id.name

            # Get the names of the selected values (e.g. ["S", "M", "L"])
            value_names = [value.name for value in attribute_line.value_ids]

            # Add to the attributes dictionary
            attributes[attribute_name] = value_names

        return attributes

    def shorepos_category_create_or_retrieve(self: models.Model, odoo_category: models.Model) -> int | None:
        """Create or retrieve a Shore POS category."""

        self.ensure_one()

        if not odoo_category:
            return None

        try:
            response = self.shorepos_api_request(method='get', endpoint='categories', params={'limit': 100})
            shorepos_categories = {category['name']: category['id'] for category in response['data']}

            shorepos_category_id = shorepos_categories.get(odoo_category.name)

            if not shorepos_category_id:
                response = self.shorepos_api_request(method='post', endpoint='categories', json={'name': odoo_category.name})
                _logger.info(f'Created new Odoo product category in Shore POS: {response.get("name")}')
                shorepos_category_id = response.get('id')

            return shorepos_category_id

        except Exception as error:
            _logger.error(f'Failed to create or retrieve Odoo category in Shore POS: {odoo_category}: {error}')
            return None

    def shorepos_tax_rate_create_or_retrieve(self: models.Model, odoo_tax_rate: float | int) -> int | None:
        """Create or retrieve an Shore POS tax rate."""

        self.ensure_one()

        if odoo_tax_rate is None:
            return None

        odoo_tax_rate = Decimal(str(odoo_tax_rate))

        try:
            response = self.shorepos_api_request(method='get', endpoint='taxes')
            shorepos_tax_rates = {Decimal(tax['tax_rate']): tax['id'] for tax in response}

            shorepos_tax_rate_id = shorepos_tax_rates.get(odoo_tax_rate)

            if not shorepos_tax_rate_id:
                response = self.shorepos_api_request(method='post', endpoint='taxes', json={'name': f'{odoo_tax_rate}%', 'tax_rate': odoo_tax_rate})
                _logger.info(f'Created new Odoo tax rate in Shore POS: {response.get("name")}')
                shorepos_tax_rate_id = response.get('id')

            return shorepos_tax_rate_id

        except Exception as error:
            _logger.error(f'Failed to create or retrieve Odoo tax rate in Shore POS: {odoo_tax_rate}%: {error}')
            return None

    def shorepos_upload_image(self: models.Model, image: str):
        """Uploads an image to Shore POS."""

        self.ensure_one()

        if not image:
            return None

        try:
            # Decode the image from Base64
            image = b64decode(image)

            # Guess the file type from the decoded data
            image_file_type = filetype.guess(image)

            if not image_file_type:
                _logger.error('Failed to determine Odoo product image type')
                return None

            # Convert if .webp
            if image_file_type.mime == 'image/webp':
                try:
                    # Encode the image back to PNG format
                    is_success, buffer = cv2.imencode('.png', cv2.imdecode(np.frombuffer(buffer=image, dtype=np.uint8), cv2.IMREAD_UNCHANGED))

                    if is_success:
                        # Get the PNG byte data
                        image = buffer.tobytes()
                    else:
                        _logger.error('Failed to convert Odoo product image from .webp to .png')
                        return None

                    # Update the file type to reflect the new PNG format
                    image_file_type = SimpleNamespace(mime='image/png', extension='png')

                except Exception as error:
                    _logger.error(f'Failed to convert Odoo product image from .webp to .png. Error: {error}')
                    return None

            response = self.shorepos_api_request(method='post', endpoint='images', files={'image': (f'product_image{image_file_type.extension}', BytesIO(image), f'{image_file_type.mime}'), 'type': (None, 'product')})

            shorepos_image_id = response.get('id')

            _logger.info(f'Uploaded a new Odoo product image to Shore POS (Shore POS image ID {shorepos_image_id})')

            return shorepos_image_id

        except Exception as error:
            _logger.error(f'Failed to upload Odoo product image to Shore POS: {error}')
            return None

    def odoo_shorepos_products_stock_quantity_sync(self: models.Model, odoo_product: models.Model, shorepos_products_stock_map: dict[int, dict[str, Any]]) -> None:
        self.ensure_one()
        # Store Shore POS product ID in a list after Odoo data has been pushed to Shore POS
        shorepos_product_ids_updated = {}

        product_shorepos_id = int(odoo_product.shorepos_id) or int(odoo_product.product_tmpl_id.shorepos_id)

        # Determine the corresponding Shore POS stock info
        shorepos_stock_info = shorepos_products_stock_map.get(product_shorepos_id)

        if not shorepos_stock_info:
            return shorepos_product_ids_updated

        # Shore POS product stock quantity
        shorepos_stock_quantity = float(shorepos_stock_info['quantity'])

        # Odoo product stock quant
        odoo_product_stock_quant = self.env['stock.quant'].search(
            [
                ('product_tmpl_id.shorepos_store_identifier', '=', self.settings_shorepos_store_identifier),
                ('product_id', '=', odoo_product.id),
                ('location_id', '=', self.settings_shorepos_products_warehouse_location.lot_stock_id.id),
            ],
            limit=1,
        )

        if odoo_product_stock_quant and shorepos_stock_quantity == odoo_product.qty_available:
            return shorepos_product_ids_updated

        # Get last update dates
        odoo_stock_quantity_last_update = getattr(odoo_product_stock_quant, 'stock_quantity_last_update', None) if odoo_product_stock_quant else None
        odoo_stock_quantity_last_update = odoo_stock_quantity_last_update if isinstance(odoo_stock_quantity_last_update, datetime) else fields.datetime.min

        shorepos_date_modified_gmt = datetime.fromisoformat(shorepos_stock_info['time_modified'])
        shorepos_date_modified_gmt = shorepos_date_modified_gmt.astimezone(UTC).replace(tzinfo=None) if isinstance(shorepos_date_modified_gmt, datetime) else fields.datetime.min

        woocommerce_last_sync = getattr(odoo_product, 'woocommerce_last_sync', None)
        woocommerce_last_sync = woocommerce_last_sync if isinstance(woocommerce_last_sync, datetime) else fields.datetime.min

        # Determine the latest timestamp among all sources
        latest_timestamp = max(odoo_stock_quantity_last_update, shorepos_date_modified_gmt, woocommerce_last_sync)

        # If Shore POS is the most recent source of truth, update Odoo
        if latest_timestamp == shorepos_date_modified_gmt:
            if odoo_product_stock_quant:
                odoo_product_stock_quant.with_context(from_external_sync=True).with_company(self.env.company).write({'quantity': shorepos_stock_quantity})
                _logger.info(
                    f'Updated Shore POS product stock quantity in Odoo: {odoo_product.name} (Odoo product ID: {odoo_product.id}, Shore POS product ID: {product_shorepos_id}) - Stock quantity: {shorepos_stock_quantity}'
                )

            else:
                self.env['stock.quant'].create(
                    {
                        'shorepos_store_identifier': self.settings_shorepos_store_identifier,
                        'product_id': odoo_product.id,
                        'quantity': shorepos_stock_quantity,
                        'location_id': self.settings_shorepos_products_warehouse_location.lot_stock_id.id,
                    }
                )
                _logger.info(
                    f'Created Shore POS product stock quantity object in Odoo: {odoo_product.name} (Odoo product ID: {odoo_product.id}, Shore POS product ID: {product_shorepos_id}) - Stock quantity: {shorepos_stock_quantity}'
                )

            # Update the stock last sync
            odoo_product.shorepos_stock_last_sync_update(shorepos_date_modified_gmt)

        # If Odoo is the most recent source, update Shore POS
        else:
            stock_payload = {
                'stock': {
                    'quantity': str(odoo_product.qty_available - shorepos_stock_quantity),
                    'date': datetime.now().strftime('%d.%m.%Y'),
                }
            }

            # The Shore POS endpoint 'products/{product_shorepos_id}/adjust_inventory' does not provide the 'time_modified field' on API Version 13
            response = self.shorepos_api_request(method='put', endpoint=f'products/{product_shorepos_id}/adjust_inventory', json=stock_payload)

            _logger.info(
                f'Updated Odoo product stock quantity in Shore POS: {odoo_product.name} (Odoo product ID: {odoo_product.id}, Shore POS product ID: {product_shorepos_id}) - Stock quantity: {odoo_product.qty_available}. Shore POS response: {response}'
            )

            # Add the Shore POS product ID to the list of updated products
            shorepos_product_ids_updated[odoo_product.id] = product_shorepos_id

        return shorepos_product_ids_updated

    def odoo_shorepos_products_stock_quantity_sync_batch(self: models.Model) -> None:
        """Synchronize stock quantity levels between Shore POS and Odoo using 'product.product records'. In Shore POS, if a stock quantity level changes due to a purchase, the 'time_modified' field is updated accordingly."""

        self.ensure_one()

        # Shore POS parameters
        params = {}

        # Retrieve last sync timestamp from the log model
        shorepos_stock_sync_log = self.env['shorepos.stock.sync.log'].search([], limit=1)
        if shorepos_stock_sync_log:
            params['start_date'] = (
                f'{shorepos_stock_sync_log.odoo_shorepos_last_sync.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]}Z'  # Has no effect on the "products" endpoint; Only supported by the "products/delta/modified" endpoint, which is unreliable with API version 13
            )

        # Fetch all products from Shore POS
        shorepos_products = self.shorepos_api_request_all(method='get', endpoint='products', params=params)
        shorepos_products_map = {shorepos_product['id']: shorepos_product for shorepos_product in shorepos_products}

        # Fetch all Odoo 'product.product' records linked to Shore POS
        if version_info[0] == 16:
            odoo_products = self.env['product.product'].search(
                [
                    ('product_tmpl_id.shorepos_store_identifier', '=', self.settings_shorepos_store_identifier),
                    ('product_tmpl_id.sync_to_shorepos', '=', True),
                    ('product_tmpl_id.active', '=', True),
                    ('product_tmpl_id.shorepos_id', '!=', False),
                    ('detailed_type', '=', 'product'),
                ]
            )

        elif version_info[0] == 18:
            odoo_products = self.env['product.product'].search(
                [
                    ('product_tmpl_id.shorepos_store_identifier', '=', self.settings_shorepos_store_identifier),
                    ('product_tmpl_id.sync_to_shorepos', '=', True),
                    ('product_tmpl_id.active', '=', True),
                    ('product_tmpl_id.shorepos_id', '!=', False),
                    ('is_storable', '=', True),
                ]
            )

        shorepos_product_ids_updated = {}

        for odoo_product in odoo_products:
            shorepos_product_ids_updated.update(self.odoo_shorepos_products_stock_quantity_sync(odoo_product, shorepos_products_map))

        # After all syncs, fetch timestamps for all Shore POS products whose stock was updated from Odoo
        if shorepos_product_ids_updated:
            # Fetch all products again to get the updated 'time_modified'
            shorepos_products = self.shorepos_api_request_all(method='get', endpoint='products')
            shorepos_products_map = {shorepos_product['id']: shorepos_product for shorepos_product in shorepos_products}

            for odoo_product in odoo_products:
                shorepos_id = shorepos_product_ids_updated.get(odoo_product.id)
                if shorepos_id and shorepos_id in shorepos_products_map:
                    shorepos_product_data = shorepos_products_map[shorepos_id]

                    # Update the stock last sync
                    odoo_product.shorepos_stock_last_sync_update(datetime.fromisoformat(shorepos_product_data['time_modified']).astimezone(UTC).replace(tzinfo=None))
                    _logger.info(f'Updated Shore POS product sync timestamp into Odoo: {odoo_product.name} (Odoo product ID: {odoo_product.id}, Shore POS ID: {shorepos_id})')

    @api.model
    def odoo_to_shorepos_products_delete(self: models.Model) -> None:
        # Odoo search conditions
        search_conditions = [('shorepos_store_identifier', '=', self.settings_shorepos_store_identifier), ('sync_to_shorepos', '=', False), ('shorepos_id', '!=', False)]

        # Odoo products
        odoo_products = self.env['product.template'].search(search_conditions)

        for odoo_product in odoo_products:
            try:
                self.shorepos_api_request(method='delete', endpoint=f'products/{odoo_product.shorepos_id}')

                # If deletion is successful, clear the Shore POS fields in Odoo
                odoo_product.write({'shorepos_store_identifier': False, 'shorepos_id': False, 'odoo_to_shorepos_last_sync': False, 'shorepos_stock_last_sync': False})
                _logger.info(f'Deleted Odoo product from Shore POS: {odoo_product.name} (Odoo product ID: {odoo_product.id})')

            except requests.exceptions.HTTPError as error:
                if error.response.status_code == 404:
                    _logger.warning(
                        f'Not found Odoo product in Shore POS: {odoo_product.name} (Odoo product ID: {odoo_product.id}, Shore POS product ID: {odoo_product["shorepos_id"]}); Clearing Shore POS fields in Odoo'
                    )
                    # If it's already gone from Shore POS, just clear the Shore POS fields in Odoo
                    odoo_product.write({'shorepos_store_identifier': False, 'shorepos_id': False, 'odoo_to_shorepos_last_sync': False, 'shorepos_stock_last_sync': False})
                else:
                    _logger.exception(f'HTTPError while deleting Odoo product from Shore POS: {odoo_product.name} (Odoo product ID: {odoo_product.id}, Shore POS product ID: {odoo_product["shorepos_id"]}): {error}')
            except Exception as error:
                _logger.exception(f'Error while deleting Odoo product from Shore POS: {odoo_product.name} (Odoo product ID: {odoo_product.id}, Shore POS product ID: {odoo_product["shorepos_id"]}): {error}')

    @api.model
    def odoo_to_shorepos_products_sync(self: models.Model) -> None:
        # Odoo search conditions
        search_conditions = [('sync_to_shorepos', '=', True), ('active', '=', True), ('default_code', '!=', False)]

        if self.settings_shorepos_odoo_to_shorepos_products_language_code:
            search_conditions.append(('product_language_code', '=', self.settings_shorepos_odoo_to_shorepos_products_language_code))

        # Odoo products
        odoo_products = self.env['product.template'].search(search_conditions) | self.env['product.product'].search(search_conditions + [('product_tmpl_id.default_code', '!=', False)]).mapped('product_tmpl_id')

        # Sync if modified or never synced
        odoo_products_to_sync = odoo_products.filtered(lambda odoo_product: (not odoo_product.odoo_to_shorepos_last_sync or odoo_product.odoo_to_shorepos_last_sync < odoo_product['write_date']))

        for odoo_product in odoo_products_to_sync:
            try:
                if odoo_product.default_code and len(odoo_product.default_code) > 30:
                    _logger.info(
                        f'Skipped import of Odoo product into Shore POS: {odoo_product.name} (Odoo product ID: {odoo_product.id}). "default_code" exceeds 30 characters; Shore POS limits "product_code", "ean", and "gtin" to a maximum of 30 characters.'
                    )
                    continue

                # Determine categories: check for multi-category field, otherwise use default category
                categories = []
                if hasattr(odoo_product, 'categ_ids') and odoo_product.categ_ids:
                    categories = [{'id': self.shorepos_category_create_or_retrieve(category)} for category in odoo_product.categ_ids if self.shorepos_category_create_or_retrieve(category)]
                elif odoo_product.categ_id:
                    category_id = self.shorepos_category_create_or_retrieve(odoo_product.categ_id)
                    if category_id:
                        categories.append({'id': category_id})

                # Upload and get ID for the main product image
                shorepos_image_id = None
                if self.settings_shorepos_images_sync and odoo_product.image_1920:
                    shorepos_image_id = self.shorepos_upload_image(odoo_product.image_1920)

                # Build the product payload, ensuring the order matches the POST documentation
                product_values = {
                    'name': odoo_product.name,
                    'description': odoo_product.description_sale or '',
                    'product_code': odoo_product.default_code or '',
                    'ean': odoo_product.default_code or '',
                    'gtin': odoo_product.default_code or '',
                    'price': float(
                        odoo_product.taxes_id.compute_all(
                            price_unit=odoo_product.list_price,
                            currency=odoo_product.currency_id,
                            quantity=1.0,
                            product=odoo_product,
                            partner=self.env['res.partner'],
                            is_refund=False,
                            handle_price_include=True,
                            include_caba_tags=False,
                            rounding_method=None,
                        )['total_excluded']
                    ),
                    'purchase_price': float(odoo_product.standard_price),
                    'custom_price': False,
                    'tax_type': self.shorepos_tax_rate_create_or_retrieve(odoo_product.taxes_id[0].amount) if odoo_product.taxes_id else None,
                    'quantity': float(odoo_product.qty_available),
                    'attributes': {},
                    'categories': categories or None,
                    'images': [{'id': shorepos_image_id, 'new': True}] if shorepos_image_id else None,
                    'package_size_value': (
                        float(odoo_product.packaging_ids[0].qty)
                        if odoo_product.packaging_ids
                        else 1
                        if (odoo_product.uom_id and odoo_product.uom_id.name == 'Units') or self.settings_shorepos_products_package_size_unit_default == 'pc'
                        else None
                    ),
                    'package_size_unit': (
                        'pc'
                        if odoo_product.uom_id and odoo_product.uom_id.name == 'Units'
                        else (odoo_product.uom_id.name.lower() if odoo_product.uom_id and odoo_product.uom_id.name.lower() in ['ml', 'l', 'g', 'kg', 'm', 'm2', 'm3', 'pc'] else None)
                        if self.settings_shorepos_products_package_size_unit_default == 'odoo'
                        else self.settings_shorepos_products_package_size_unit_default
                    ),
                    'brand': odoo_product.product_brand_id.name if odoo_product.product_brand_id else None,
                    'supplier': odoo_product.seller_ids[0].name.name if odoo_product.seller_ids else None,
                    'reorder_level': float(odoo_product.reordering_min_qty),
                    'safety_stock': float(odoo_product.reordering_max_qty),
                    'is_giftcard': False,
                    'custom_sale': False,
                    'is_favourite': False,
                    'in_shop': False,
                }

                # Handle variations
                if len(odoo_product.product_variant_ids) > 1:
                    product_values['attributes'] = {}
                    variations_data = []
                    for odoo_product_variant in odoo_product.product_variant_ids:
                        # Upload and get ID for the main product image
                        shorepos_image_id = None
                        if self.settings_shorepos_images_sync and odoo_product_variant.image_1920:
                            shorepos_image_id = self.shorepos_upload_image(odoo_product_variant.image_1920)

                        variant_attributes = {ptav.attribute_id.name: ptav.name for ptav in odoo_product_variant.product_template_attribute_value_ids}

                        product_variations_values = {
                            'name': odoo_product_variant.name,
                            'product_code': odoo_product_variant.default_code or '',
                            'ean': odoo_product_variant.default_code or '',
                            'gtin': odoo_product_variant.default_code or '',
                            'price': float(
                                odoo_product_variant.taxes_id.compute_all(
                                    price_unit=odoo_product_variant.list_price,
                                    currency=odoo_product_variant.currency_id,
                                    quantity=1.0,
                                    product=odoo_product_variant,
                                    partner=self.env['res.partner'],
                                    is_refund=False,
                                    handle_price_include=True,
                                    include_caba_tags=False,
                                    rounding_method=None,
                                )['total_excluded']
                            ),
                            'purchase_price': float(odoo_product_variant.standard_price),
                            'tax_type': self.shorepos_tax_rate_create_or_retrieve(odoo_product_variant.taxes_id[0].amount) if odoo_product_variant.taxes_id else None,
                            'quantity': float(odoo_product_variant.qty_available),
                            'attributes': variant_attributes,
                            'images': [{'id': shorepos_image_id, 'new': True}] if shorepos_image_id else None,
                            'package_size_value': (
                                float(odoo_product_variant.packaging_ids[0].qty)
                                if odoo_product_variant.packaging_ids
                                else 1
                                if (odoo_product_variant.uom_id and odoo_product_variant.uom_id.name == 'Units') or self.settings_shorepos_products_package_size_unit_default == 'pc'
                                else None
                            ),
                            'package_size_unit': (
                                'pc'
                                if odoo_product_variant.uom_id and odoo_product_variant.uom_id.name == 'Units'
                                else (odoo_product_variant.uom_id.name.lower() if odoo_product_variant.uom_id and odoo_product_variant.uom_id.name.lower() in ['ml', 'l', 'g', 'kg', 'm', 'm2', 'm3', 'pc'] else None)
                                if self.settings_shorepos_products_package_size_unit_default == 'odoo'
                                else self.settings_shorepos_products_package_size_unit_default
                            ),
                            'reorder_level': float(odoo_product_variant.reordering_min_qty),
                            'safety_stock': float(odoo_product_variant.reordering_max_qty),
                            'is_giftcard': False,
                        }

                        variations_data.append(product_variations_values)

                    product_values['variations'] = variations_data

                # Single product
                else:
                    product_values['attributes'] = self.shorepos_attributes_build(odoo_product)
                    product_values.pop('variations', None)

                # Determine whether to create or update the product
                if odoo_product.shorepos_id:
                    try:
                        response = self.shorepos_api_request(method='put', endpoint=f'products/{odoo_product.shorepos_id}', json=product_values)
                        _logger.info(f'Updated Odoo product in Shore POS: {odoo_product.name} (Odoo product ID: {odoo_product.id}, Shore POS product ID: {odoo_product["shorepos_id"]})')

                    # If the product was not found (404), we treat it as a new product and create it
                    except requests.exceptions.HTTPError as error:
                        if error.response.status_code == 404:
                            _logger.warning(f'Not found Shore POS product: {odoo_product.name} (Odoo product ID: {odoo_product.id}, Shore POS product ID {odoo_product.shorepos_id}); Re-creating as a new product')
                            response = self.shorepos_api_request(method='post', endpoint='products', json=product_values)
                            _logger.info(f'Imported Odoo product into Shore POS: {odoo_product.name} (Odoo product ID: {odoo_product.id}, Shore POS product ID: {response.get("id")}). Shore POS response: {response}')
                        else:
                            raise error

                else:
                    response = self.shorepos_api_request(method='post', endpoint='products', json=product_values)
                    _logger.info(f'Imported Odoo product into Shore POS: {odoo_product.name} (Odoo product ID: {odoo_product.id}, Shore POS product ID: {response.get("id")}). Shore POS response: {response}')

                odoo_product.write(
                    {
                        'shorepos_store_identifier': self.settings_shorepos_store_identifier,
                        'shorepos_id': response.get('id'),
                        'odoo_to_shorepos_last_sync': fields.Datetime.now(),
                    }
                )

                # Handle variant IDs for newly created variable products
                if len(odoo_product.product_variant_ids) > 1:
                    shorepos_variants = {variation.get('product_code'): variation for variation in response.get('variations', [])}
                    for odoo_product_variant in odoo_product.product_variant_ids:
                        shorepos_variant = shorepos_variants.get(odoo_product_variant.default_code)
                        if shorepos_variant and shorepos_variant.get('id'):
                            odoo_product_variant.write({'shorepos_store_identifier': self.settings_shorepos_store_identifier, 'shorepos_id': shorepos_variant['id'], 'odoo_to_shorepos_last_sync': fields.Datetime.now()})

            except requests.exceptions.HTTPError as error:
                _logger.exception(f'HTTPError syncing product {odoo_product.id} to Shore POS: {error}')

            except Exception as error:
                _logger.exception(f'Error syncing product {odoo_product.id} to Shore POS: {error}')
