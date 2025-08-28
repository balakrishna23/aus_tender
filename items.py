# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

import scrapy


class AusItem(scrapy.Item):
    # Define the fields for AusTender data
    atm_id = scrapy.Field()
    agency = scrapy.Field()
    category = scrapy.Field()
    close_date_time = scrapy.Field()
    publish_date = scrapy.Field()
    location = scrapy.Field()
    atm_type = scrapy.Field()
    description = scrapy.Field()
    conditions_for_participation = scrapy.Field()
    contact = scrapy.Field()
    atm_documents_link = scrapy.Field()
    url = scrapy.Field()
