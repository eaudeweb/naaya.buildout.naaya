"""Generate pip requirements and Zope instance files for a Naaya site.

Usage: naaya_pip.py SITE_CFG requirements|config
"""
import configparser
import os
import re
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, 'templates')
BASE_CFG = os.path.join(os.path.dirname(HERE), 'base.cfg')
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
    cfg.read(path, encoding='utf-8')
    return cfg


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


def norm(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def requirements(cfg):
    site = cfg['site']
    develop = split(site.get('develop', ''))
    names = {norm(project_name(src)) for src in develop}
    # compat puts the source directory on sys.path, as buildout develop eggs;
    # extend_path namespaces such as Products.NaayaContent need that
    lines = ['-e %s --config-settings editable_mode=compat' % src
             for src in develop]
    for egg in ALWAYS + split(site.get('eggs', '')):
        if norm(re.split(r'[\[<>=]', egg)[0]) not in names:
            lines.append(egg)
    write(os.path.join(site['directory'], 'requirements.txt'), lines)
    # Packages installed from source must not be pinned
    skip = ' '.join('--skip %s' % project_name(src) for src in develop)
    sys.stdout.write(skip + '\n')


def write(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


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
    instances = [s.split(':', 1)[1] for s in cfg.sections()
                 if s.startswith('instance:')]
    for name in instances:
        opts = dict(cfg['site'])
        opts.update(cfg['instance:%s' % name])
        home = os.path.join(root, 'parts', name)
        etc = os.path.join(home, 'etc')
        log_dir = opts.get('log-directory', os.path.join(root, 'var', 'log'))
        environment = '\n'.join(
            ' '.join(line.split(None, 1)) for line in split(opts.get('environment', '')))
        write(os.path.join(etc, 'zope.conf'), [render(
            'zope.conf', instance_home=home,
            client_home=opts.get('client-home', os.path.join(root, 'var', name)),
            debug_mode=opts.get('debug-mode', 'off'),
            environment=indent(environment),
            main_db=main_db(opts), temporary_db=temporary_db(opts),
            additional=opts.get('zope-conf-additional', '')).rstrip()])
        write(os.path.join(etc, 'wsgi.ini'), [render(
            'wsgi.ini', http_address=listen_address(opts['http-address']),
            threads=opts.get('threads', '4'), zope_conf=os.path.join(etc, 'zope.conf'),
            event_log=opts.get('event-log', os.path.join(log_dir, name + '.log')),
            access_log=opts.get('access-log',
                                os.path.join(log_dir, name + '-access.log'))).rstrip()])
        write(os.path.join(etc, 'site.zcml'), [render('site.zcml').rstrip()])
        package_includes(os.path.join(etc, 'package-includes'),
                         split(opts.get('zcml', '')), opts.get('zcml-additional', ''))
        script = os.path.join(root, 'bin', name)
        write(script, [render('instance.sh', name=name, root=root, etc=etc).rstrip()])
        os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR | stat.S_IXGRP)
        # migrate.mk runs parts/<instance>/bin/interpreter
        interpreter = os.path.join(home, 'bin', 'interpreter')
        os.makedirs(os.path.dirname(interpreter), exist_ok=True)
        if os.path.lexists(interpreter):
            os.remove(interpreter)
        os.symlink(os.path.join(root, '.venv', 'bin', 'python'), interpreter)
        for path in (log_dir, os.path.join(root, 'var', name),
                     os.path.join(root, 'var', 'cache')):
            os.makedirs(path, exist_ok=True)
        sys.stdout.write('Wrote %s and bin/%s\n' % (etc, name))
    # [scripts] name = module:function(arguments), as zc.recipe.egg scripts
    if cfg.has_section('scripts'):
        for name, entry in cfg['scripts'].items():
            module, call = entry.strip().split(':', 1)
            script = os.path.join(root, 'bin', name)
            write(script, [render('script.py', root=root, module=module,
                                  call=call).rstrip()])
            os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR | stat.S_IXGRP)
            sys.stdout.write('Wrote bin/%s\n' % name)
    if os.path.exists(os.path.join(root, '.venv', 'bin', 'nytest')):
        script = os.path.join(root, 'bin', 'nytest')
        write(script, [render('nytest.py', root=root).rstrip()])
        os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR | stat.S_IXGRP)


def main():
    cfg = read_site(sys.argv[1])
    {'requirements': requirements, 'config': config}[sys.argv[2]](cfg)


if __name__ == '__main__':
    main()
