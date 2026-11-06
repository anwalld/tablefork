#!/usr/bin/env python3
"""Exercise V7 with real Flyway/MySQL, isolated from every existing stack.

Run from the repo root: python3 tests/ai_personas/run_schema_tests.py
Uses existing local images by default. No ports, production volumes or .env.
"""

import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]


def main():
    evidence = Path(tempfile.mkdtemp(prefix='cppai-personas-schema-', dir='/private/tmp' if Path('/private/tmp').exists() else None))
    os.chmod(evidence, 0o700)
    name = 'cppai-personas-schema-' + secrets.token_hex(6)
    database_container = name + '-mysql'
    password = secrets.token_hex(24)
    env = {**os.environ, 'MYSQL_PASSWORD': password, 'MYSQL_ROOT_PASSWORD': secrets.token_hex(24), 'FLYWAY_PASSWORD': password}
    log = (evidence/'run.log').open('w', buffering=1)
    status = 1
    summary = {'scope':'isolated schema only', 'evidence':str(evidence), 'steps':[],
               'mysql_image':'mysql:8.4.6', 'flyway_image':'flyway/flyway:11.14.1',
               'test_image':os.environ.get('PERSONA_SCHEMA_TEST_IMAGE','cppai-chat-tests:latest')}

    def run(label, args, *, input=None, check=True, timeout=180):
        started = time.monotonic()
        result = subprocess.run(args, input=input, capture_output=True, text=True, env=env, timeout=timeout)
        # Never echo commands, environment, or MySQL initialization logs.
        out = (result.stdout + result.stderr).replace(password,'[REDACTED]').replace(env['MYSQL_ROOT_PASSWORD'],'[REDACTED]')
        log.write(f'\n[{label}] exit={result.returncode}\n'+out)
        if label != 'readiness':
            summary['steps'].append({'step':label,'exit':result.returncode,'seconds':round(time.monotonic()-started,3)})
        if check and result.returncode:
            raise RuntimeError(f'{label} failed; see {evidence}/run.log')
        return result

    def flyway(database, command='migrate', target=None):
        label = f'{database}-{command}' + (f'-target-{target}' if target else '')
        args = ['docker','run','--rm','--network',name,'-e','FLYWAY_PASSWORD',
                '-e',f'FLYWAY_URL=jdbc:mariadb://mysql:3306/{database}?allowPublicKeyRetrieval=true&useSsl=false',
                '-e','FLYWAY_USER=chat','-v',f'{ROOT}/migrations:/flyway/sql:ro','flyway/flyway:11.14.1',command]
        if target: args.append(f'-target={target}')
        run(label,args)

    def probe(database, command):
        run(f'{database}-'+('pytest' if command[0]=='-m' else 'fixture'),
            ['docker','run','--rm','--network',name,'-e','MYSQL_HOST=mysql','-e','MYSQL_PASSWORD',
             '-e',f'MYSQL_DATABASE={database}','-e','EVIDENCE_DIR=/evidence',
             '-e','PYTHONDONTWRITEBYTECODE=1','-v',f'{ROOT}:/work:ro','-v',f'{evidence}:/evidence',
             '--entrypoint','python',summary['test_image'],*command])

    print(f'Evidence: {evidence}',flush=True)
    try:
        run('create-network',['docker','network','create',name])
        run('start-mysql',['docker','run','-d','--name',database_container,'--network',name,'--network-alias','mysql',
                          '--mount','type=tmpfs,destination=/var/lib/mysql',
                          '-e','MYSQL_ROOT_PASSWORD','-e','MYSQL_PASSWORD','-e','MYSQL_USER=chat',
                          '-e','MYSQL_DATABASE=chat_upgrade','mysql:8.4.6',
                          '--character-set-server=utf8mb4','--collation-server=utf8mb4_bin'])
        ready = False
        for _ in range(60):
            result = run('readiness',['docker','exec',database_container,'sh','-c',
                                      'MYSQL_PWD="$MYSQL_PASSWORD" mysql -h127.0.0.1 -uchat -e "SELECT 1" chat_upgrade'],check=False,timeout=10)
            if result.returncode==0:
                ready=True
                break
            time.sleep(1)
        if not ready: raise RuntimeError('disposable mysql did not become ready')
        run('create-fresh-database',['docker','exec','-i',database_container,'sh','-c',
                                    'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot'],
            input="CREATE DATABASE chat_fresh CHARACTER SET utf8mb4 COLLATE utf8mb4_bin; GRANT ALL ON chat_fresh.* TO 'chat'@'%';\n")
        # A clean database and a genuine V6 database take different paths.
        flyway('chat_fresh')
        flyway('chat_upgrade',target='6')
        probe('chat_upgrade',['/work/tests/ai_personas/upgrade_fixture.py'])
        flyway('chat_upgrade')
        for database in ('chat_fresh','chat_upgrade'):
            flyway(database)  # No migration should run twice.
            flyway(database,'validate')
            probe(database,['-m','pytest','-q','-p','no:cacheprovider','/work/tests/ai_personas/test_schema.py'])
        summary['source_sha256'] = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                                    for directory in ('migrations','tests/ai_personas')
                                    for p in sorted((ROOT/directory).rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
        status=0
    except Exception as error:
        summary['error']=str(error)
        print(str(error),flush=True)
    finally:
        # Only the random names created by this invocation can be removed.
        for label,args in [('remove-mysql',['docker','rm','-fv',database_container]),
                           ('remove-network',['docker','network','rm',name])]:
            try: run(label,args,check=False,timeout=30)
            except Exception as error: summary.setdefault('cleanup_errors',[]).append(str(error))
        if summary.get('cleanup_errors') or any(s['exit'] for s in summary['steps'] if s['step'].startswith('remove-')):
            status=1
        summary['exit']=status
        (evidence/'result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        (evidence/'run.exit').write_text(str(status)+'\n')
        log.close()
    print(f'Schema validation exit={status}; details: {evidence}/result.json',flush=True)
    return status


if __name__=='__main__':
    raise SystemExit(main())
