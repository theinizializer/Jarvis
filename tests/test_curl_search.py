"""Regressions from the real chat: ads, asset manifests and translations aren't product prices."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from web_scraper import extract,search_links
from web_module import WebModule,_handle_generico


class CurlSearchTests(unittest.TestCase):
    def test_search_drops_ads_help_links_and_unwraps_only_organic_results(self):
        html='''<html><a class="result-link" href="https://duckduckgo.com/y.js?ad_domain=amazon.it">Shop</a>
        <a class="result-link" href="https://duckduckgo.com/duckduckgo-help-pages/company/ads-by-microsoft">Ads</a>
        <div class="sponsored"><a class="result-link" href="https://shop.test/ad">Sponsor</a></div>
        <a class="result-link" href="/l/?uddg=https%3A%2F%2Fwww.amazon.it%2Fdp%2FB012345678">Product</a></html>'''
        self.assertEqual(search_links(html,'https://lite.duckduckgo.com/lite/'),[{'url':'https://www.amazon.it/dp/B012345678','title':'Product'}])

    def test_numeric_ui_ranges_and_translations_are_not_prices(self):
        body='''<html><title>RayNeo Air 4 Pro</title><script>window.assets={"app.js":"https://cdn.test/app.js","price.css":"https://cdn.test/price.css"};
        window.state={"translations":{"low-price":"129 EUR","high-price":"305 EUR","price":"Prezzo"},"filters":{"price":129}};</script></html>'''
        result=extract(body,'https://www.amazon.it/stores/example','RayNeo Air 4 Pro prezzo')
        self.assertEqual(result['status'],'no_data');self.assertFalse(result['price_evidence'])
        self.assertNotIn('app.js',json.dumps(result['facts']));self.assertNotIn('129',json.dumps(result['facts']))

    def test_real_price_survives_large_metadata_before_product_data(self):
        data={**{f'asset{i}':f'https://cdn.test/{i}.js' for i in range(130)},'product':{'name':'RayNeo Air 4 Pro','price':279.99,'priceCurrency':'EUR'}}
        result=extract(json.dumps(data),'https://example.test','RayNeo Air 4 Pro prezzo')
        self.assertEqual(result['status'],'ok');self.assertTrue(any(f['value']==279.99 for f in result['price_evidence']))

    def test_json_product_numbers_are_not_confused(self):
        data={'products':[{'name':'RayNeo Air 3 Pro','price':129},{'name':'RayNeo Air 4 Pro','price':279.99}]}
        result=extract(json.dumps(data),'https://example.test','RayNeo Air 4 Pro prezzo')
        self.assertIn('279.99',json.dumps(result));self.assertNotIn('129',json.dumps(result['facts']))

    def test_html_product_cards_keep_name_with_their_price(self):
        body='''<html><div data-asin="B000000003"><h2>RayNeo Air 3 Pro</h2><span class="a-price">129 EUR</span></div>
        <div data-asin="B000000004"><h2>RayNeo Air 4 Pro</h2><span class="a-price">279.99 EUR</span></div></html>'''
        result=extract(body,'https://www.amazon.it/s?k=rayneo','RayNeo Air 4 Pro prezzo')
        self.assertEqual(result['status'],'ok');self.assertNotIn('129',json.dumps(result))
        self.assertTrue(any(f.get('product')=='RayNeo Air 4 Pro' for f in result['price_evidence']))

    def test_headset_price_excludes_delivery_and_compatible_accessories(self):
        body='''<html><div data-asin="B000000004"><h2>RayNeo Air 4 Pro Occhiali AR</h2><span class="a-price"><span class="a-offscreen">279.99 EUR</span></span>Consegna 5.86 EUR</div>
        <div data-asin="B000000005"><h2>Cavo per RayNeo Air 4 Pro</h2><span class="a-price">15 EUR</span></div>
        <div data-asin="B000000006"><h2>RayNeo Sound Tube per Air 4 Pro</h2><span class="a-price">16 EUR</span></div>
        <div data-asin="B000000007"><h2>Paraluce con aletta per RayNeo Air 4 Pro</h2><span class="a-price">17 EUR</span></div>
        <div data-asin="B000000008"><h2>RayNeo Air 4 Occhiali</h2><span class="a-price">99 EUR</span></div></html>'''
        result=extract(body,'https://www.amazon.it/s?k=rayneo','RayNeo Air 4 Pro prezzo')
        amounts=[f['value'] for f in result['price_evidence']]
        self.assertEqual(amounts,['279.99 EUR'])
        self.assertEqual(result['price_evidence'][0]['product_url'],'https://www.amazon.it/dp/B000000004')

    def test_reference_price_and_delivery_alone_are_not_the_product_offer(self):
        body='''<html><div data-asin="B000000004"><h2>RayNeo Air 4 Pro Occhiali AR</h2>
        <span class="a-price a-text-price"><span class="a-offscreen">399 EUR</span></span>
        <span class="a-price"><span class="a-offscreen">279.99 EUR</span></span>Consegna 5.86 EUR</div></html>'''
        result=extract(body,'https://www.amazon.it/s?k=rayneo','RayNeo Air 4 Pro prezzo')
        self.assertEqual([f['value'] for f in result['price_evidence']],['279.99 EUR'])
        delivery='<html><div data-asin="B000000004"><h2>RayNeo Air 4 Pro</h2>Consegna 5.86 EUR</div></html>'
        self.assertEqual(extract(delivery,'https://www.amazon.it/s?k=rayneo','RayNeo Air 4 Pro prezzo')['status'],'no_data')

    def test_product_page_uses_main_offer_not_a_related_products_price(self):
        body='''<html><title>RayNeo Air 4 Pro Occhiali</title><div id="corePriceDisplay_desktop_feature_div"><span class="a-price priceToPay"><span class="a-offscreen">279.99 EUR</span></span></div>
        <div data-asin="B000000005"><h2>RayNeo Air 4 Pro Occhiali</h2><span class="a-price">199 EUR</span></div></html>'''
        result=extract(body,'https://www.amazon.it/dp/B000000004','RayNeo Air 4 Pro prezzo')
        self.assertEqual([f['value'] for f in result['price_evidence']],['279.99 EUR'])
        related='<html><title>RayNeo Air 4 Pro</title><div data-asin="B000000005"><h2>RayNeo Air 4 Pro</h2><span class="a-price">199 EUR</span></div></html>'
        self.assertEqual(extract(related,'https://www.amazon.it/dp/B000000004','RayNeo Air 4 Pro prezzo')['status'],'no_data')

    def test_price_markup_after_two_megabytes_is_still_parsed(self):
        body='<html><title>RayNeo Air 4 Pro</title>'+(' '*(2*1024*1024+100))+'<main><p class="price">279.99 EUR</p></main></html>'
        result=extract(body,'https://www.amazon.it/dp/B000000004','RayNeo Air 4 Pro prezzo')
        self.assertEqual(result['status'],'ok');self.assertTrue(result['price_evidence'])

    def test_wrong_product_page_is_not_a_verified_price_for_the_requested_model(self):
        body='<html><title>RayNeo Air 3 Pro</title><main><p class="price">129 EUR</p></main></html>'
        self.assertEqual(extract(body,'https://example.test','RayNeo Air 4 Pro prezzo')['status'],'no_data')

    def test_nested_numeric_price_value_keeps_its_currency_and_source_path(self):
        data={'name':'RayNeo Air 4 Pro','offers':{'price':{'value':279.99,'currency':'EUR'}}}
        result=extract(json.dumps(data),'https://example.test','RayNeo Air 4 Pro prezzo')
        self.assertEqual(result['status'],'ok')
        self.assertTrue(any(f['field']=='$.offers.price.value' and f['value']==279.99 for f in result['price_evidence']))

    def test_site_filter_excludes_other_domains_and_ad_endpoints(self):
        rss='''<rss><channel><item><link>https://other.test/product</link><title>Other</title></item>
        <item><link>https://www.amazon.it/dp/B000000004</link><title>RayNeo</title></item></channel></rss>'''
        with patch('web_module._fetch',side_effect=['<html></html>',rss,'<html><p class="price">279.99 EUR</p></html>']) as fetch:
            output=_handle_generico('RayNeo prezzo site:amazon.it')
        urls=[call.args[0] for call in fetch.call_args_list]
        self.assertNotIn('https://other.test/product',urls);self.assertIn('279.99 EUR',output)


if __name__=='__main__':unittest.main()
