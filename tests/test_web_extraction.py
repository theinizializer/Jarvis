"""Query extraction retains useful values and provenance; challenge pages are never facts."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from web_scraper import extract,script_data,search_links
from web_module import WebModule,_handle_generico


class ExtractionTests(unittest.TestCase):
    def test_html_jsonld_and_initial_state_remove_navigation_and_tracking(self):
        body='''<html><title>Catalogo</title><nav>Offerte pubblicitarie 999 euro</nav>
        <main><h1>ESP32</h1><p class="price">Prezzo 12.50 EUR</p>
        <script type="application/ld+json">{"name":"ESP32","offers":{"price":12.5,"priceCurrency":"EUR"}}</script>
        <script>window.__INITIAL_STATE__={"product":{"name":"ESP32","stock":7,"analytics":{"price":9999}}};</script>
        </main><footer>privacy e cookie</footer></html>'''
        result=extract(body,'https://example.test','ESP32 prezzo disponibilita')
        rendered=json.dumps(result)
        self.assertEqual(result['status'],'ok');self.assertFalse(result['javascript_executed'])
        self.assertIn('12.5',rendered);self.assertIn('stock',rendered)
        self.assertIn('$.offers.price',rendered);self.assertNotIn('9999',rendered)
        self.assertNotIn('pubblicitarie',rendered);self.assertNotIn('privacy',rendered)

    def test_json_arrays_include_multiple_matches_and_their_paths(self):
        data={'flights':[{'flightNumber':'AA101','price':12},{'flightNumber':'BB202','price':24}], 'analytics':{'price':999}}
        result=extract(json.dumps(data),'https://example.test','voli prezzo')
        text=json.dumps(result)
        self.assertIn('$.flights[0].price',text);self.assertIn('$.flights[1].price',text)
        self.assertNotIn('999',text)

    def test_requested_custom_fields_are_not_mistaken_for_product_names(self):
        result=extract('{"name":"LAB-SENSOR","sku":"CHECK-123","price":37.25}', 'https://example.test','nome prezzo sku')
        self.assertIn('CHECK-123',json.dumps(result));self.assertEqual(result['status'],'ok')

    def test_named_product_filter_excludes_other_product_records(self):
        data={'products':[{'name':'ESP32','price':12},{'name':'ESP8266','price':999}]}
        result=extract(json.dumps(data),'https://example.test','ESP32 prezzo')
        self.assertIn('12',json.dumps(result));self.assertNotIn('999',json.dumps(result))

    def test_static_javascript_data_source_is_followed_without_executing_code(self):
        bodies=['<html><div id="root"></div><script src="/app.js"></script></html>',
                "fetch('/api/product.json');",'{"name":"LAB-SENSOR","price":37.25,"sku":"CHECK-123"}']
        with patch('web_module._fetch',side_effect=bodies) as fetch:
            result=WebModule().read_url('https://example.test/page','nome prezzo sku')
        self.assertEqual(result['status'],'ok');self.assertFalse(result['javascript_executed'])
        self.assertEqual(result['url'],'https://example.test/api/product.json')
        self.assertEqual(result['discovered_from'],'https://example.test/page')
        self.assertEqual(fetch.call_count,3)

    def test_javascript_assignments_and_json_parse_are_data_not_executed(self):
        script='window.state={"name":"ESP32","price":19}; process.exit(1);'
        self.assertEqual(list(script_data(script)),[{'name':'ESP32','price':19}])
        parsed="window.state=JSON.parse('{\"price\":21}');"
        self.assertEqual(list(script_data(parsed)),[{'price':21}])

    def test_static_javascript_object_literals_and_scalars_are_parsed_without_eval(self):
        result=list(script_data("window.state={name:'ESP32',price:19.5, currency:'EUR',}; runUnknownCode();"))
        self.assertIn({'name':'ESP32','price':19.5,'currency':'EUR'},result)
        scalars=list(script_data("const price=21; const currency='EUR'; const password='secret';"))
        self.assertEqual(scalars,[{'price':21,'currency':'EUR'}])
        self.assertFalse(list(script_data('window.state={price:calculateSecret()};')))

    def test_challenge_page_is_error_and_not_cached(self):
        challenge='<html>Please complete the following challenge: Select all squares containing a duck</html>'
        self.assertEqual(extract(challenge,'https://example.test')['status'],'blocked')
        web=WebModule()
        with patch('web_module._fetch',return_value=challenge):
            for _ in range(2):self.assertEqual(web.search_result('ricerca ordinaria')['status'],'error')
        self.assertFalse(web._cache)

    def test_user_url_is_fetched_directly_with_query_filter_and_source(self):
        with patch('web_module._fetch',return_value='{"name":"board","price":25,"telemetry":{"price":999}}') as fetch:
            result=WebModule().search_result('prezzo https://example.test/product')
        fetch.assert_called_once_with('https://example.test/product')
        self.assertEqual(result['status'],'ok');self.assertIn('25',result['output']);self.assertNotIn('999',result['output'])

    def test_search_visits_source_and_extracts_data_not_the_results_page(self):
        listing='<html><a class="result-link" href="https://example.test/product">ESP32 shop</a></html>'
        with patch('web_module._fetch',side_effect=[listing,'<html><main><h1>ESP32</h1><p class="price">12 EUR</p></main></html>']) as fetch:
            result=_handle_generico('ESP32 prezzo')
        self.assertEqual(fetch.call_args.args[0],'https://example.test/product')
        self.assertIn('Fonte: https://example.test/product',result);self.assertIn('12 EUR',result)

    def test_empty_javascript_shell_reports_no_data(self):
        result=extract('<html><div id="root"></div><script src="/app.js"></script></html>','https://example.test','prezzo')
        self.assertEqual(result['status'],'no_data');self.assertIn('JavaScript',result['error'])

    def test_fetch_rejects_local_files_and_credentials(self):
        for url in ('file:///etc/passwd','https://user:password@example.test','--config'):
            with self.subTest(url=url),self.assertRaises(ValueError):WebModule().read_url(url)


if __name__=='__main__':unittest.main()
