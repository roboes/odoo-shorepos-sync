from datetime import datetime
from typing import Any

from odoo import fields, models


# Stock
class StockQuant(models.Model):
    _inherit = 'stock.quant'

    # Override the existing 'product_id' field to add the ondelete cascade
    product_id = fields.Many2one(comodel_name='product.product', string='Product', domain=lambda self: self._domain_product_id(), required=True, index=True, check_company=True, ondelete='cascade')

    shorepos_store_identifier = fields.Char(string='Shore POS Store Identifier', readonly=True, index=True)

    stock_quantity_last_update = fields.Datetime(string='Stock Quantity Last Update', readonly=True, copy=False)

    def write(self: models.Model, values: dict[str, Any]) -> bool:
        """Overrides the standard write method to update a timestamp only when the stock quantity is changed manually by a user."""
        if 'quantity' in values and not self.env.context.get('from_stock_move') and not self.env.context.get('from_external_sync'):
            values['stock_quantity_last_update'] = fields.Datetime.now()

        return super().write(values)


# Product
class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # Override the existing 'default_code' field to remove the compute/inverse for multi-variant products, so it can be set manually
    default_code = fields.Char(string='Internal Reference', store=True)

    shorepos_id = fields.Char(string='Shore POS Product ID', readonly=True, index=True)

    shorepos_store_identifier = fields.Char(string='Shore POS Store Identifier', readonly=True, index=True)
    odoo_to_shorepos_last_sync = fields.Datetime(string='Odoo to Shore POS Product Last Sync', readonly=True)
    sync_to_shorepos = fields.Boolean(string='Sync to Shore POS', default=False)

    shorepos_stock_last_sync = fields.Datetime(string='Stock Date Updated', readonly=True)


# Product variations
class ProductProduct(models.Model):
    _inherit = 'product.product'

    shorepos_id = fields.Char(string='Shore POS Product Variation ID', readonly=True, index=True)

    shorepos_store_identifier = fields.Char(string='Shore POS Store Identifier', readonly=True, index=True)
    odoo_to_shorepos_last_sync = fields.Datetime(string='Odoo to Shore POS Product Variation Last Sync', readonly=True)

    shorepos_stock_last_sync = fields.Datetime(string='Stock Date Updated', readonly=True)

    def shorepos_stock_last_sync_update(self: models.Model, timestamp: datetime) -> None:
        """Updates the 'shorepos_stock_last_sync' field for both the product and its template directly via SQL to avoid updating the 'write_date'."""

        self.ensure_one()

        # Update the product.product record using a parameterized query
        self.env.cr.execute(query='UPDATE product_product SET shorepos_stock_last_sync = %s WHERE id = %s', params=(timestamp, self.id))

        # Update the product.template record using a parameterized query
        self.env.cr.execute(query='UPDATE product_template SET shorepos_stock_last_sync = %s WHERE id = %s', params=(timestamp, self.product_tmpl_id.id))

        # Invalidate the cache for the modified records to ensure consistency
        self.env['product.product']._invalidate_cache(ids=[self.id])
        self.env['product.template']._invalidate_cache(ids=[self.product_tmpl_id.id])


class ShoreposSyncLog(models.Model):
    _name = 'shorepos.sync.log'
    _description = 'Shore POS Sync Log'

    odoo_shorepos_last_sync = fields.Datetime(string='Sync Date', readonly=True)


class ShoreposStockSyncLog(models.Model):
    _name = 'shorepos.stock.sync.log'
    _description = 'Shore POS Stock Sync Log'

    odoo_shorepos_last_sync = fields.Datetime(string='Sync Date', readonly=True)
