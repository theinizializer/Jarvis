"""Wake aliases recovered from v9, shared by voice entry points without loading audio models."""
import re

_WAKE_VARIANTS = {
    'jarvis', 'jarvi', 'jarvs', 'jarbi', 'jarwis', 'javis', 'jarves', 'jarfis',
    'zarvis', 'garvis', 'harvis', 'giorvis', 'giorvi', 'giervi', 'giorviz',
    'jervis', 'jervi', 'yervis', 'gervis', 'gervi', 'djarvis', 'djarvi',
    # v9's complete explicit alias list, plus the truncated "jarv" heard by STT.
    'jarviz', 'jarvin', 'jarvix', 'giarvis', 'yarvis', 'sarvis', 'parvis',
    'carvis', 'arvis', 'jerbi', 'gerbi', 'jarvid', 'iarviss', 'iarviz',
    'giarviz', 'heyjarvis', 'eijarvis', 'jarv',
}
_RE_WAKE = re.compile(r'\b(?:' + '|'.join(sorted(_WAKE_VARIANTS, key=len, reverse=True)) + r'|(?:jar|gar|ger|gio|gie|jer|yer|djar)[a-z]{1,5})\b', re.I)
_RE_SLEEP = re.compile(r'\b(dormi|sleep|dors|schlaf)\b', re.I)
_GREETING = {'hey', 'ehi', 'hei', 'ei', 'hi', 'ciao', 'salut', 'ok', 'okay', 'hallo', 'bonjour', 'salve'}
_SLEEP_COMMANDS = {'dormi', 'vai a dormire', 'mettiti in standby', 'vai in standby',
                   'standby', 'sleep', 'go to sleep', 'dors', 'schlaf'}


def _lev(a, b):
    previous = list(range(len(b)+1))
    for row, char in enumerate(a, 1):
        current = [row]
        for col, other in enumerate(b, 1):
            current.append(min(current[-1]+1, previous[col]+1, previous[col-1]+(char != other)))
        previous = current
    return previous[-1]


def _wake_token(token):
    return token in _WAKE_VARIANTS or (len(token) >= 4 and _lev(token, 'jarvis') <= 2)


def is_wake_word(text):
    return any(_wake_token(token) for token in re.findall(r'[^\W\d_]+', text.casefold()))


def is_sleep_word(text):
    return bool(_RE_SLEEP.search(text))


def strip_wake_word(text):
    return re.sub(r'^[,\s!]+', '', _RE_WAKE.sub('', text).strip()).strip()


def _wake_prefix(text):
    tokens = list(re.finditer(r'[^\W\d_]+', text.casefold()))
    index = 1 if tokens and tokens[0].group() in _GREETING else 0
    if len(tokens) > index and _wake_token(tokens[index].group()):
        return True, text[tokens[index].end():].lstrip(' ,.!?:;')
    return False, text


class VoiceDialogue:
    def __init__(self):
        self.sleeping = False

    def route(self, text):
        text = text.strip()
        wake, command = _wake_prefix(text)
        normalized = re.sub(r'[^\w\s]', '', command.casefold()).strip()
        if normalized in _SLEEP_COMMANDS and (wake or not self.sleeping):
            self.sleeping = True
            return {'action': 'sleep', 'text': ''}
        if self.sleeping and not wake:
            return {'action': 'ignored', 'text': ''}
        if wake:
            self.sleeping = False
            return {'action': 'wake', 'text': command}
        return {'action': 'command', 'text': text}
