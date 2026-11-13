import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from provider_context import pack_messages,estimate_tokens


class ContextTests(unittest.TestCase):
    def test_old_complete_turns_removed_without_altering_active_task_or_archive(self):
        history=[{'role':'system','content':'RULES'}]
        for i in range(5):history.extend([
            {'role':'user','content':f'old {i}'},
            {'role':'assistant','tool_calls':[{'id':str(i),'function':{'name':'read_file','arguments':'{}'}}]},
            {'role':'tool','tool_call_id':str(i),'content':'old data '*500},
            {'role':'assistant','content':'done'}])
        start=len(history)
        active=[{'role':'user','content':'ACTIVE TASK ALL INSTRUCTIONS'},
                {'role':'assistant','tool_calls':[{'id':'active','function':{'name':'read_file','arguments':'{}'}}]},
                {'role':'tool','tool_call_id':'active','content':'ACTIVE EVIDENCE'}]
        history.extend(active);original=copy.deepcopy(history)
        packed,info=pack_messages(history,turn_start=start,budget=1200)
        self.assertGreater(info['omitted_messages'],0)
        self.assertEqual(packed[-3:],active);self.assertEqual(history,original)
        self.assertLess(estimate_tokens(packed),1500)
        ids={c['id'] for m in packed for c in m.get('tool_calls',[])}
        self.assertTrue(all(m['tool_call_id'] in ids for m in packed if m['role']=='tool'))

    def test_current_task_is_never_silently_clipped_to_fit_budget(self):
        messages=[{'role':'system','content':'rules'},{'role':'user','content':'x'*30000}]
        result,info=pack_messages(messages,budget=1000)
        self.assertEqual(result,messages);self.assertEqual(info['omitted_messages'],0)
        self.assertGreater(info['estimated_input_tokens'],1000)
