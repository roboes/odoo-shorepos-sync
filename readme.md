# Odoo-Shore POS Sync

<p align="center">
  <img src="./media/odoo-shorepos-sync-logo.png" alt="Odoo-Shore POS Sync" width="80%" height="auto">
</p>

<br>

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://buymeacoffee.com/roboes)

## Description

The **Odoo-Shore POS Sync** add-on enables synchronization between [Shore POS](https://www.shore.com/en/pos/) and Odoo. The main features are:

- **Odoo to Shore POS:** Synchronize new and existing products (including variations) and stock quantity levels.
- **Automated and Manual Synchronization:** A built-in cron job scheduler enables regular synchronization, complemented by a dedicated button for manually triggering updates.
- **Image Synchronization:** Optionally synchronize product images from Odoo to Shore POS.

Some features require additional setup, as detailed in the [Requirements](#requirements) section.

> [!WARNING]
> This add-on is provided without any warranty and may contain bugs as it is a recently developed solution. Testing in a controlled environment is recommended before deployment, and usage is at one's own risk.

## Requirements

### Odoo

#### Python Dependencies

Install the necessary Python packages by running:

```sh
python -m pip install filetype numpy opencv-python
```

#### Odoo Add-ons (Required)

> [!TIP]
> To automatically download and install the required and optional Odoo add-ons listed below, follow the instructions in [odoo-module-dependency-installer.md](./installation/odoo-module-dependency-installer.md).

> [!TIP]
> To automatically apply the Odoo configuration listed below, follow the instructions in [odoo-settings-configuration.md](./installation/odoo-settings-configuration.md).

- **Sales** (`sale_management`)
  - Enable [Product Variants](https://www.odoo.com/documentation/16.0/applications/sales/sales/products_prices/products/variants.html):
    - `Home Menu` > `Settings` > `Sales` > `Product Catalog` > Enable `Variants`.
- **Inventory** (`stock`)
  - Enable Delivery Methods:
    - `Home Menu` > `Settings` > `Inventory` > `Shipping` > Enable `Delivery Methods`.
  - (Optional) Enable [Product Packagings](https://www.odoo.com/documentation/16.0/applications/inventory_and_mrp/inventory/product_management/configure/packaging.html):
    - `Home Menu` > `Settings` > `Inventory` > `Products` > Enable `Product Packagings`.
  - Enable Units of Measure:
    - `Home Menu` > `Settings` > `Inventory` > `Products` > Enable `Units of Measure`.
  - (Optional) Set up a dedicated warehouse for Shore POS sales:
    - `Home Menu` > `Settings` > `Inventory` > `Warehouse` > Enable `Storage Locations` and configure under `Locations` the warehouse accordingly.
- **Job Queue** (`queue_job`)
  - [GitHub](https://github.com/OCA/queue/tree/16.0/queue_job) | [Odoo Apps Store](https://apps.odoo.com/apps/modules/16.0/queue_job) (requires additional [configuration instructions](https://github.com/OCA/queue/tree/16.0/queue_job#configuration)).

#### Odoo Add-ons (Optional)

While not mandatory, the following Odoo Community Association (OCA) add-ons are recommended to enhance functionality:

- **Module Auto Update** (`module_auto_update`): Automatically updates installed modules to their latest versions, ensuring the system remains current with minimal manual intervention.
  - [GitHub](https://github.com/OCA/server-tools/tree/16.0/module_auto_update) | [Odoo Apps Store](https://apps.odoo.com/apps/modules/16.0/module_auto_update)
- **Scheduled Actions as Queue Jobs** (`queue_job_cron`): Extends the functionality of `queue_job` and allows to run an Odoo cron as a queue job.
  - [GitHub](https://github.com/OCA/queue/tree/16.0/queue_job_cron) | [Odoo Apps Store](https://apps.odoo.com/apps/modules/16.0/queue_job_cron)
- **Product - Many Categories** (`product_multi_category`): Enhances the standard single-category assignment (`categ_id`) by introducing a `categ_ids` field, allowing products to be organized into multiple categories.
  - [GitHub](https://github.com/OCA/product-attribute/tree/16.0/product_multi_category) | [Odoo Apps Store](https://apps.odoo.com/apps/modules/16.0/product_multi_category)
- **Product Brand Manager** (`product_brand`): Adds a `product_brand_id` field to facilitate the export and management of product brands (only one brand per product allowed).
  - [GitHub](https://github.com/OCA/brand/tree/16.0/product_brand) | [Odoo Apps Store](https://apps.odoo.com/apps/modules/16.0/product_brand)

## Installation

Follow these steps to install the Odoo-Shore POS Sync add-on:

1. **Install Python Dependencies:** Ensure the [Python dependencies](#python-dependencies) are installed on the Odoo instance.
2. **Enable Odoo Add-ons:** Install and activate all [required](#odoo-add-ons-required) and, if applicable, [optional](#odoo-add-ons-optional) Odoo add-ons.
3. **Add the Add-on:** Download and place the [`shorepos_sync`](./shorepos_sync) directory into the Odoo `addons` directory.
4. **Activate Debug Mode:** Log in to Odoo and enable [Debug Mode](https://www.odoo.com/documentation/16.0/applications/general/developer_mode.html).
5. **Update the Apps List:** Navigate to `Home Menu` > `Apps` and click **Update Apps List**.
6. **Activate the Add-on:** Use the filter to search for `shorepos_sync` and activate the add-on.

## Configuration

The add-on is configured through the Shore POS Sync configuration, accessible via `Home Menu` > `Shore POS Sync`.

### Shore POS API Credentials

1. Shore POS Manager > [API & Apps](https://manager.shore.com/api-apps).
2. Enter the name of your app (e.g. `Odoo`) and press on `Add App`.
3. Press in the newly created app and press `Generate new token`.
4. Give the access rights (read, create, update, delete) to each category accordingly.
5. Copy the `Client ID`, `Client Secret` and `Refresh Token` variables.

## Disclaimer

This module is an independent third-party integration. It is not affiliated with, endorsed by, or sponsored by Odoo S.A. or Shore GmbH.

All copyrights and trademarks are the property of their respective owners.

## References

- [Shore POS API Documentation](https://api-docs.inventorum.com/index.html)

## See also

- [Odoo-WooCommerce Sync](https://github.com/roboes/odoo-woocommerce-sync): Connector add-on for Odoo that synchronizes data between WooCommerce and Odoo.
