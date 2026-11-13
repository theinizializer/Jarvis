import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from skills import kyglove_workshop as w
from skills.kyglove_device import Device
from app_config import Settings
from jarvis_v10 import Jarvis


class WorkshopTests(unittest.TestCase):
    @unittest.skipUnless((w.ROOT/'firmware_OTA/KYGlove_OTA5_GIROSCOPIO.bin').is_file(),'Richiede clone KyGlove preparato')
    def test_failed_case_blocks_publication_and_repair_keeps_draft(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(w,'STORE',Path(tmp)):
            source = 'namespace lamp { inline void tick(unsigned long, bool) {} }'
            tests = 'bool nominale(){return true;} bool limite(){return false;} bool ripetizione(){return true;}'
            rev = w.add(w.BASE,'lamp',source,tests)['revision']
            draft = w.directory(rev)/'modules.json'
            original = draft.read_bytes()
            with self.assertRaisesRegex(ValueError,'limite'):
                w.build(rev)
            self.assertFalse((draft.parent/'verified').exists())
            with self.assertRaises(FileNotFoundError):
                w.release(rev)
            other = w.add(w.BASE,'lamp',source,tests.replace('false','true'))
            self.assertNotEqual(other['revision'],rev)
            self.assertEqual(draft.read_bytes(),original)
            with self.assertRaises(ValueError):
                w.directory('../KYGlove_MPU6050')

    @unittest.skipUnless((w.ROOT/'firmware_OTA/KYGlove_OTA5_GIROSCOPIO.bin').is_file(),'Richiede clone KyGlove preparato')
    def test_restore_stable_checks_reboot_and_slot(self):
        device = Device('kyglove.local')
        state = dict(version='jarvis-previous',pending=False,protocol='KYGlove-OTA-v1:ESP32S3',slotSize=1966080)
        device.request = Mock(side_effect=[state,dict(version=w.BASE,pending=True),dict(version=w.BASE,pending=False)])
        device.post = Mock()
        cancel = Mock()
        cancel.is_set.return_value=False
        result=device.change_image('restore_base',None,cancel)
        self.assertEqual(result['version'],w.BASE)
        path,body,headers=device.post.call_args.args
        self.assertEqual(path,'/update')
        self.assertIn((w.ROOT/'firmware_OTA/KYGlove_OTA5_GIROSCOPIO.bin').read_bytes(),body)
        self.assertEqual(int(headers['X-KyGlove-Size']), (w.ROOT/'firmware_OTA/KYGlove_OTA5_GIROSCOPIO.bin').stat().st_size)
        device.request=Mock(return_value={**state,'slotSize':10000})
        with self.assertRaisesRegex(ValueError,'Spazio'):
            device.change_image('restore_base',None,cancel)
        self.assertEqual(device.post.call_count,1)

    def test_agent_skill_and_clock_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            host=Jarvis(settings=Settings(Path(tmp)))
            try:
                host.role='admin'
                host.session_user='test-admin'
                host.enable_skill('KyGlove')
                self.assertIn('kyglove_build',[t['function']['name'] for t in host.available_tools()])
                runtime=host._agent_runtime()
                runtime.state={'kyglove':True}
                with self.assertRaises(PermissionError):
                    runtime._tool('execute_terminal_command',{'command':'date'},True)
                runtime.state=None
                args={'provider':'groq','problem':'Il caso limite fallisce'}
                self.assertTrue(runtime._tool('kyglove_consult',args,False).startswith('__NEEDS_CONFIRM__'))
                with patch.object(host,'api_credentials',return_value=[Mock(value='dummy')]), patch.object(host,'_chat_completion',return_value={'content':'Controlla il bordo'}) as call:
                    self.assertEqual(runtime._tool('kyglove_consult',args,True)['review'],'Controlla il bordo')
                    self.assertEqual(call.call_args.kwargs['provider'],'groq')
                runtime.state=None
                self.assertRegex(host._process('Che ore sono?'),r'Sono le \d\d:\d\d')
                cmd='date +%H:%M'
                self.assertTrue(host._execute(cmd,agent=True).startswith('__NEEDS_CONFIRM__'))
                self.assertEqual(host._execute(cmd,agent=True,confirmed=True)['returncode'],0)
                self.assertEqual(host._execute(cmd,agent=True)['returncode'],0)
                self.assertIn(cmd,Settings(Path(tmp)).values['approved_clock_reads']['test-admin'])
                self.assertTrue(host._execute(cmd+'; date',agent=True).startswith('__NEEDS_CONFIRM__'))
                host.session_user='another-admin'
                self.assertTrue(host._execute(cmd,agent=True).startswith('__NEEDS_CONFIRM__'))
            finally:
                host._shutdown()
