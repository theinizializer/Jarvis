"""Tools registered only for the enabled admin skill."""

from skills import kyglove_workshop as workshop
from skills.kyglove_device import Device
from task_io import TaskCancelled

ACTIONS = ('status', 'settings', 'diagnostics', 'configure', 'upload', 'rollback', 'restore_base')


def schemas(schema, string):
    version = string('ID della revisione additiva.')
    return [
        schema('kyglove_read', 'Leggi una guida, un modulo del core o le aggiunte di una revisione.',
               {'file':string('README.md, nome di un modulo del core, oppure ID revisione.')}, ['file']),
        schema('kyglove_add', 'Crea una revisione separata con una nuova funzione e i suoi tre test.',
               {'parent':string('Versione OTA5 di base o ID della revisione genitore verificata.'),
                'name':string('Namespace C++ univoco.'),'code':string('Header C++ con tick.'),
                'tests':string('Tre funzioni bool: nominale, limite, ripetizione.')},
               ['parent','name','code','tests']),
        schema('kyglove_build', 'Esegui test e compilazione della revisione. Pubblica il binario solo dopo il successo.',
               {'revision':version}, ['revision']),
        schema('kyglove_device', 'Monitora, configura, installa oppure ripristina il guanto.',
               {'action':{'type':'string','enum':list(ACTIONS)},
                'address':{'type':'string','enum':['kyglove.local','192.168.4.1']}, 'revision':version,
                'changes':{'type':'object','description':'Parametri da cambiare, t0–t4, cooldown, window, filter, gain, deadzone, hysteresis, invertX/Y.'},
                'persist':{'type':'boolean','description':'Salva le regolazioni al riavvio.'}}, ['action']),
        schema('kyglove_consult', 'Chiedi un parere di programmazione sul frammento e sul test fallito.',
               {'provider':{'type':'string','enum':['auto','nvidia','groq']},
                'problem':string('Solo codice della nuova funzione, errore e comportamento atteso.')}, ['problem'])]


def invoke(host, tool, args, confirmed):
    if host.voice_mode and host.execution_role != 'admin':
        return '__NEEDS_CONFIRM__' + tool
    host.require_admin()
    if not host.skills.is_enabled('KyGlove'):
        raise PermissionError('Attiva la skill KyGlove nelle impostazioni.')
    action = args.get('action')
    readonly = tool == 'kyglove_read' or tool == 'kyglove_device' and action in ACTIONS[:3]
    if not readonly and not confirmed:
        return '__NEEDS_CONFIRM__' + tool
    if tool == 'kyglove_read':
        file = args['file']
        if file == 'README.md':
            return {'text': (workshop.ROOT / file).read_text()}
        if file in {p.name for p in (workshop.ROOT/workshop.SKETCH).iterdir()
                    if p.is_file() and p.suffix in {'.h','.cpp','.ino'} and p.name != 'GloveWiFiConfig.h'}:
            return {'text': (workshop.ROOT/workshop.SKETCH/file).read_text()}
        return {'modules':workshop.read_modules(file)}
    if tool == 'kyglove_add':
        return workshop.add(args['parent'],args['name'],args['code'],args['tests'])
    if tool == 'kyglove_build':
        return workshop.build(args['revision'], host._agent_cancel)
    if tool == 'kyglove_device' and action in ACTIONS:
        device = Device(args.get('address','kyglove.local'))
        if action in ACTIONS[:3]:
            return device.request('/'+action)
        if action == 'configure':
            return device.configure(args['changes'],args.get('persist',False))
        return device.change_image(action,args.get('revision'),host._agent_cancel)
    if tool == 'kyglove_consult':
        preference = args.get('provider','auto')
        if preference not in {'auto','nvidia','groq'}:
            raise ValueError('Provider non supportato.')
        problem = args['problem']
        if not 1 <= len(problem) <= 12000:
            raise ValueError('Limita la richiesta al frammento e al test fallito.')
        msgs = [{'role':'system','content':'Revisore C++ embedded ESP32-S3. Spiega il difetto e proponi una modifica solo alla nuova estensione. Nessuna azione esterna.'},
                {'role':'user','content':problem}]
        providers = ['nvidia','groq'] if preference == 'auto' else [preference]
        errors = []
        for provider in providers:
            model = 'qwen/qwen3.5-397b-a17b' if provider == 'nvidia' else host.model_for('groq')
            keys = host.api_credentials(provider,model)
            if not keys:
                errors.append(provider+': chiave non configurata')
                continue
            try:
                result = host._chat_completion(msgs,provider=provider,model=model,api_key=keys[0].value)
                if not result.get('content'):
                    raise ValueError('Risposta vuota')
                return {'provider':provider,'model':model,'review':result['content']}
            except TaskCancelled:
                raise
            except Exception as error:
                errors.append(f'{provider}/{model}: {type(error).__name__}')
        raise ValueError('Consulenza indisponibile. ' + ', '.join(errors))
    raise ValueError('Operazione KyGlove sconosciuta.')
