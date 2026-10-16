"""Generate pip requirements and Zope instance files for a Naaya site.

Usage: naaya_pip.py SITE_CFG requirements|venvs|config|haproxy-version
"""
import configparser
import os
import re
import stat
import sys

from make_constraints import collect

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, 'templates')
BASE_CFG = os.path.join(os.path.dirname(HERE), 'base.cfg')
VERSIONS_CFG = os.path.join(os.path.dirname(HERE), 'zope6-versions.cfg')
# Installed on every site, on top of the eggs lists
ALWAYS = ['Zope[wsgi]', 'ZEO', 'zdaemon']


def split(value):
    return [line.split('#')[0].strip() for line in value.splitlines()
            if line.split('#')[0].strip()]


def read_site(path):
    """Read SITE_CFG, with [naaya] eggs and zcml taken from base.cfg."""
    base = configparser.ConfigParser(interpolation=None, strict=False)
    base.optionxform = str
    base.read(BASE_CFG, encoding='utf-8')
    cfg = configparser.ConfigParser(
        interpolation=configparser.ExtendedInterpolation(), strict=False)
    cfg.optionxform = str
    cfg['naaya'] = {'eggs': base.get('naaya', 'eggs'),
                    'zcml': base.get('naaya', 'zcml')}
    root = os.path.dirname(os.path.abspath(path))
    cfg['site'] = {'directory': root, 'naaya-buildout': os.path.dirname(HERE)}
    # secret.cfg next to SITE_CFG holds [secrets], as in the buildouts
    cfg.read([path, os.path.join(root, 'secret.cfg')], encoding='utf-8')
    return cfg


def zeoserver_options(cfg):
    """[zeoserver] options with the standard paths under var/ as defaults."""
    var = os.path.join(cfg['site']['directory'], 'var')
    opts = {'zeo-address': os.path.join(var, 'zeo.socket'),
            'file-storage': os.path.join(var, 'filestorage', 'Data.fs'),
            'blob-storage': os.path.join(var, 'blobstorage')}
    opts.update(cfg['zeoserver'] if cfg.has_section('zeoserver') else {})
    return opts


def instances(cfg):
    """(name, options) per [instance:NAME]; instance options override [site]."""
    var = os.path.join(cfg['site']['directory'], 'var')
    zeo = zeoserver_options(cfg)
    result = []
    for section in cfg.sections():
        if section.startswith('instance:'):
            name = section.split(':', 1)[1]
            opts = {'venv': '.venv',
                    'client-home': os.path.join(var, name),
                    'file-storage': os.path.join(var, 'filestorage', 'Data.fs'),
                    'blob-storage': os.path.join(var, 'blobstorage')}
            opts.update(cfg['site'])
            opts.update(cfg[section])
            # ZEO clients use the [zeoserver] address and blobs unless set
            if opts.get('storage') == 'zeo' or opts.get('temporary-storage') == 'zeo':
                opts.setdefault('zeo-address', zeo['zeo-address'])
                for key in ('file-storage', 'blob-storage'):
                    if key not in cfg[section] and key not in cfg['site']:
                        opts[key] = zeo[key]
            result.append((name, opts))
    return result


def venv_files(venv):
    """requirements.txt for .venv, requirements-circa.txt for .venv-circa."""
    suffix = venv[len('.venv'):] if venv.startswith('.venv') else '-' + venv
    return 'requirements%s.txt' % suffix, 'constraints%s.txt' % suffix


def project_name(src):
    """Project name from setup.py, or the directory name."""
    setup = os.path.join(src, 'setup.py')
    if os.path.exists(setup):
        text = open(setup, encoding='utf-8').read()
        m = re.search(r"""\bname\s*=\s*['"]([^'"]+)['"]""", text)
        if m:
            return m.group(1)
        m = re.search(r"""^NAME\s*=\s*['"]([^'"]+)['"]""", text, re.M)
        if m:
            return m.group(1)
    return os.path.basename(src.rstrip('/'))


def requires(src):
    """Requirement names of a source package, from its egg-info or setup.py."""
    for entry in os.listdir(src):
        path = os.path.join(src, entry, 'requires.txt')
        if entry.endswith('.egg-info') and os.path.exists(path):
            lines = open(path, encoding='utf-8').read().split('\n[')[0]
            return [req_name(line) for line in split(lines)]
    text = open(os.path.join(src, 'setup.py'), encoding='utf-8').read()
    m = re.search(r'install_requires\s*=\s*\[(.*?)\]', text, re.S)
    if not m:
        return []
    return [req_name(r) for r in re.findall(r"""['"]([^'"]+)['"]""", m.group(1))]


def req_name(requirement):
    return norm(re.split(r'[\[<>=!~ ;]', requirement.strip())[0])


def norm(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def used_develop(develop, eggs):
    """Source packages reached from eggs through install_requires, as buildout does."""
    sources = {norm(project_name(src)): src for src in develop}
    todo = [req_name(egg) for egg in eggs]
    seen = set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        if name in sources:
            todo.extend(requires(sources[name]))
    return [src for name, src in sources.items() if name in seen]


def venvs(cfg):
    """Eggs and source packages for each venv of the site."""
    result = {}
    for name, opts in instances(cfg):
        eggs = result.setdefault(opts['venv'], {'eggs': list(ALWAYS), 'opts': opts})['eggs']
        eggs.extend(e for e in split(opts.get('eggs', '')) if e not in eggs)
    for venv, data in result.items():
        data['develop'] = used_develop(split(data['opts'].get('develop', '')), data['eggs'])
    return result


def requirements(cfg):
    root = cfg['site']['directory']
    versions_cfgs = split(cfg['site'].get('versions', VERSIONS_CFG))
    for venv, data in venvs(cfg).items():
        requirements_txt, constraints_txt = venv_files(venv)
        names = {norm(project_name(src)) for src in data['develop']}
        # compat puts the source directory on sys.path, as buildout develop eggs;
        # extend_path namespaces such as Products.NaayaContent need that
        lines = ['-e %s --config-settings editable_mode=compat' % src
                 for src in data['develop']]
        lines += [egg for egg in data['eggs'] if req_name(egg) not in names]
        write(os.path.join(root, requirements_txt), lines)
        versions = {}
        for versions_cfg in versions_cfgs:
            collect(versions_cfg, versions)
        # Packages installed from source must not be pinned; an empty pin unpins
        write(os.path.join(root, constraints_txt),
              ['%s==%s' % (name, version) for key, (name, version) in sorted(versions.items())
               if version and norm(key) not in names])
        sys.stdout.write('Wrote %s and %s\n' % (requirements_txt, constraints_txt))


def print_venvs(cfg):
    for venv in venvs(cfg):
        sys.stdout.write('%s %s %s\n' % ((venv,) + venv_files(venv)))


def write(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def write_script(path, text):
    write(path, [text.rstrip()])
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP)


def render(template, **values):
    with open(os.path.join(TEMPLATES, template), encoding='utf-8') as f:
        return f.read().format_map(values)


def indent(text, prefix='    '):
    return '\n'.join(prefix + line if line.strip() else line
                     for line in text.strip().splitlines())


def main_db(opts):
    if opts.get('storage', 'file') == 'zeo':
        storage = render('zeoclient.conf',
                         zeo_address=opts['zeo-address'],
                         cache_size=opts.get('zeo-client-cache-size', '128MB'),
                         blob_dir=opts['blob-storage'],
                         shared_blob=opts.get('shared-blob', 'on'))
    else:
        storage = render('filestorage.conf',
                         file_storage=opts['file-storage'],
                         blob_dir=opts['blob-storage'])
    return render('main_db.conf', cache_size=opts.get('zodb-cache-size', '30000'),
                  storage=indent(storage))


def temporary_db(opts):
    if opts.get('temporary-storage', 'local') == 'zeo':
        return render('temp_zeo.conf', zeo_address=opts['zeo-address'])
    return render('temp_local.conf')


def package_includes(includes, zcml, zcml_additional):
    os.makedirs(includes, exist_ok=True)
    for old in os.listdir(includes):
        os.remove(os.path.join(includes, old))
    n = 1
    for package in zcml:
        n += 1
        if '-' in package:
            package, suffix = package.split('-')
        else:
            suffix = 'configure'
        write(os.path.join(includes, '%03d-%s-%s.zcml' % (n, package, suffix)),
              ['<include package="%s" file="%s.zcml" />' % (package, suffix)])
    write(os.path.join(includes, '998-naaya-overrides.zcml'),
          [render('naaya-overrides.zcml').rstrip()])
    if zcml_additional.strip():
        write(os.path.join(includes, '999-additional-overrides.zcml'),
              [render('additional-overrides.zcml',
                      body=indent(zcml_additional)).rstrip()])


def listen_address(address):
    """A bare port, as in buildout http-address, listens on all interfaces."""
    return address if ':' in address else '0.0.0.0:' + address


def config(cfg):
    root = cfg['site']['directory']
    nytest = []
    for name, opts in instances(cfg):
        venv = os.path.join(root, opts['venv'])
        home = os.path.join(root, 'parts', name)
        etc = os.path.join(home, 'etc')
        log_dir = opts.get('log-directory', os.path.join(root, 'var', 'log'))
        # A variable without a value (an empty secret) is left out
        environment = '\n'.join(
            ' '.join(line.split(None, 1)) for line in split(opts.get('environment', ''))
            if len(line.split(None, 1)) == 2)
        write(os.path.join(etc, 'zope.conf'), [render(
            'zope.conf', instance_home=home,
            client_home=opts['client-home'],
            debug_mode=opts.get('debug-mode', 'off'),
            environment=indent(environment),
            main_db=main_db(opts), temporary_db=temporary_db(opts),
            additional=opts.get('zope-conf-additional', '')).rstrip()])
        # migrate.mk opens Data.fs directly, without ZEO and the temporary database
        write(os.path.join(etc, 'zope-migrate.conf'), [render(
            'zope.conf', instance_home=home, client_home=opts['client-home'],
            debug_mode='off', environment=indent(environment),
            main_db=main_db(dict(opts, storage='file')), temporary_db='',
            additional=opts.get('zope-conf-additional', '')).rstrip()])
        write(os.path.join(etc, 'wsgi.ini'), [render(
            'wsgi.ini', http_address=listen_address(opts['http-address']),
            threads=opts.get('threads', '4'), zope_conf=os.path.join(etc, 'zope.conf'),
            access_log_level=opts.get('access-log-level', 'INFO'),
            event_log=opts.get('event-log', os.path.join(log_dir, name + '.log')),
            access_log=opts.get('access-log',
                                os.path.join(log_dir, name + '-access.log'))).rstrip()])
        write(os.path.join(etc, 'site.zcml'), [render('site.zcml').rstrip()])
        package_includes(os.path.join(etc, 'package-includes'),
                         split(opts.get('zcml', '')), opts.get('zcml-additional', ''))
        write_script(os.path.join(root, 'bin', name),
                     render('instance.sh', name=name, root=root, etc=etc, venv=venv))
        # migrate.mk runs parts/<instance>/bin/interpreter; a symlink would not find pyvenv.cfg
        interpreter = os.path.join(home, 'bin', 'interpreter')
        if os.path.lexists(interpreter):
            os.remove(interpreter)
        write_script(interpreter, '#!/bin/sh\nexec %s/bin/python "$@"' % venv)
        paths = [log_dir, opts['client-home'], os.path.join(root, 'var', 'cache')]
        if opts.get('storage', 'file') != 'zeo':
            paths += [os.path.dirname(opts['file-storage']), opts['blob-storage']]
        for path in paths:
            os.makedirs(path, exist_ok=True)
        sys.stdout.write('Wrote %s and bin/%s\n' % (etc, name))
        # scripts: one "name = module:function(arguments)" per line
        for line in split(opts.get('scripts', '')):
            script, entry = [part.strip() for part in line.split('=', 1)]
            module, call = entry.split(':', 1)
            write_script(os.path.join(root, 'bin', script),
                         render('script.py', venv=venv, module=module, call=call))
            sys.stdout.write('Wrote bin/%s\n' % script)
        if os.path.exists(os.path.join(venv, 'bin', 'nytest')):
            nytest.append('    %s) PY=%s/bin/python ;;' % (name, venv))
    if nytest:
        write(os.path.join(root, 'bin', 'nytest.py'), [render('nytest.py').rstrip()])
        write_script(os.path.join(root, 'bin', 'nytest'),
                     render('nytest.sh', root=root, cases='\n'.join(nytest)))
    if cfg.has_section('zeoserver'):
        zeoserver(root, zeoserver_options(cfg))
    if cfg.has_section('haproxy'):
        haproxy_config(root, cfg['haproxy'])


def haproxy_config(root, opts):
    """haproxy.cfg from the haproxy.tpl shared with buildout."""
    with open(os.path.join(os.path.dirname(HERE), 'haproxy.tpl'), encoding='utf-8') as f:
        text = f.read()
    text = re.sub(r'\$\{([\w-]+)\}', lambda m: opts[m.group(1)].strip(), text)
    write(os.path.join(root, 'haproxy.cfg'), [text.rstrip()])
    sys.stdout.write('Wrote haproxy.cfg\n')


def haproxy_version(cfg):
    """Print the haproxy version and sha256 pinned in base.cfg [haproxy-build]."""
    base = configparser.ConfigParser(interpolation=None, strict=False)
    base.read(BASE_CFG, encoding='utf-8')
    sys.stdout.write('%s %s\n' % (base.get('haproxy-build', 'version'),
                                  base.get('haproxy-build', 'sha256')))


def zeoserver(root, opts):
    """parts/zeoserver/etc/zeo.conf, bin/zeoserver and bin/zeopack."""
    venv = os.path.join(root, opts.get('venv', '.venv'))
    etc = os.path.join(root, 'parts', 'zeoserver', 'etc')
    address = opts['zeo-address']
    log_dir = opts.get('log-directory', os.path.join(root, 'var', 'log'))
    write(os.path.join(etc, 'zeo.conf'), [render(
        'zeo.conf', zeo_address=address,
        pid_file=os.path.join(root, 'var', 'zeoserver.pid'),
        file_storage=opts['file-storage'], blob_dir=opts['blob-storage'],
        event_log=opts.get('event-log', os.path.join(log_dir, 'zeoserver.log'))).rstrip()])
    write_script(os.path.join(root, 'bin', 'zeoserver'),
                 render('zeoserver.sh', venv=venv, etc=etc, root=root))
    # zeopack takes a unix socket with -u, a host:port as argument
    server = address if ':' in address else '-u ' + address
    write_script(os.path.join(root, 'bin', 'zeopack'),
                 render('zeopack.sh', venv=venv, server=server,
                        pack_days=opts.get('pack-days', '7')))
    for path in (log_dir, os.path.dirname(opts['file-storage']), opts['blob-storage']):
        os.makedirs(path, exist_ok=True)
    sys.stdout.write('Wrote %s, bin/zeoserver and bin/zeopack\n' % etc)


def main():
    cfg = read_site(sys.argv[1])
    {'requirements': requirements, 'venvs': print_venvs, 'config': config,
     'haproxy-version': haproxy_version}[sys.argv[2]](cfg)


if __name__ == '__main__':
    main()
