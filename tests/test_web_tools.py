"""Queries must address the intended package and never turn API errors into facts."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from web_module import WebModule,_parse_pypi,_registry_package
from task_io import TaskCancelled


class WebToolTests(unittest.TestCase):
    def test_explicit_pypi_query_fetches_package_identifier_instead_of_whole_sentence(self):
        data={'info':{'name':'requests','version':'test-version','summary':'Test metadata'}}
        with patch('web_module._fetch_json',return_value=data) as fetch:
            result=WebModule().search('pypi requests python')
        fetch.assert_called_once_with('https://pypi.org/pypi/requests/json')
        self.assertIn('PyPI — requests vtest-version',result)

    def test_api_error_is_not_presented_as_a_package_with_an_invented_version(self):
        with patch('web_module._fetch_json',return_value={'message':'Not Found'}):
            self.assertEqual(_parse_pypi('nonexistent-test-package'),'')
            self.assertIn('Nessun risultato verificabile',WebModule().search('pypi nonexistent-test-package'))

    def test_scoped_npm_and_pypi_url_identifiers(self):
        self.assertEqual(_registry_package('npm install @scope/package','npm'),'@scope/package')
        self.assertEqual(_registry_package('https://pypi.org/project/requests/','pypi'),'requests')
        self.assertEqual(_registry_package('requests su pypi','pypi'),'requests')

    def test_failed_search_is_not_cached_and_recovery_fetches_again(self):
        web=WebModule()
        with patch('web_module._fetch_json',side_effect=[None,{'info':{'name':'requests','version':'test'}}]) as fetch:
            self.assertIn('Nessun risultato verificabile',web.search('pypi requests'))
            self.assertIn('requests vtest',web.search('pypi requests'))
        self.assertEqual(fetch.call_count,2)

    def test_stop_propagates_to_web_curl_instead_of_becoming_a_fake_search_result(self):
        event=threading.Event()
        with patch('task_io.run_command',side_effect=TaskCancelled('stop')) as run:
            with self.assertRaises(TaskCancelled):WebModule().search('pypi requests',cancel_event=event)
        self.assertIs(run.call_args.kwargs['cancel_event'],event)


if __name__=='__main__':unittest.main()
