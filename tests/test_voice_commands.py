import unittest
from voice_commands import VoiceDialogue, is_wake_word, strip_wake_word


class VoiceCommandsTests(unittest.TestCase):
    def test_v9_aliases_and_truncated_names_wake_the_session(self):
        for alias in ('jarvis','arvis','jarv','jarvi','gervis','giarviz','heyjarvis','eijarvis','iarviss'):
            with self.subTest(alias=alias):
                dialogue = VoiceDialogue()
                dialogue.route('dormi')
                result = dialogue.route('hey ' + alias + ', che ore sono?')
                self.assertEqual(result, {'action':'wake','text':'che ore sono?'})
                self.assertFalse(dialogue.sleeping)
                self.assertTrue(is_wake_word(alias))

    def test_sleep_blocks_normal_requests_until_explicit_wake(self):
        dialogue = VoiceDialogue()
        self.assertEqual(dialogue.route('Jarvis, dormi')['action'], 'sleep')
        for text in ('apri firefox','elimina i file','sto guardando un film con Jarvis','dormi'):
            self.assertEqual(dialogue.route(text), {'action':'ignored','text':''})
            self.assertTrue(dialogue.sleeping)
        self.assertEqual(dialogue.route('ehi arvis')['action'], 'wake')
        self.assertEqual(dialogue.route('apri firefox'), {'action':'command','text':'apri firefox'})

    def test_sleep_is_a_whole_command_not_a_word_inside_another_request(self):
        dialogue = VoiceDialogue()
        self.assertEqual(dialogue.route('spiegami cosa significa dormi')['action'], 'command')
        self.assertFalse(dialogue.sleeping)
        self.assertEqual(dialogue.route('/dormi')['action'], 'sleep')

    def test_alias_stripping_keeps_request_content(self):
        self.assertEqual(strip_wake_word('arvis, apri firefox'), 'apri firefox')
        self.assertEqual(strip_wake_word('jarv cerca un documento'), 'cerca un documento')


if __name__ == '__main__':
    unittest.main()
