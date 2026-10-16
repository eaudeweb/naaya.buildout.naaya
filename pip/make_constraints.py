"""Write pip constraints from buildout [versions] files, following extends."""
import argparse
import configparser
import os
import sys
import urllib.request


def read_cfg(location):
    if location.startswith(('http://', 'https://')):
        with urllib.request.urlopen(location) as response:
            text = response.read().decode('utf-8')
    else:
        with open(location, encoding='utf-8') as f:
            text = f.read()
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string(text, location)
    return parser


def collect(location, versions):
    parser = read_cfg(location)
    if parser.has_option('buildout', 'extends'):
        for parent in parser.get('buildout', 'extends').split():
            if not parent.startswith(('http://', 'https://')):
                parent = os.path.join(os.path.dirname(location), parent)
            collect(parent, versions)
    if parser.has_section('versions'):
        for name, version in parser.items('versions'):
            versions[name.lower()] = (name, version.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cfg', nargs='+', help='buildout files, later ones win')
    parser.add_argument('--skip', action='append', default=[],
                        help='package installed from source (-e), not pinned')
    args = parser.parse_args()
    versions = {}
    for cfg in args.cfg:
        collect(cfg, versions)
    skip = {name.lower() for name in args.skip}
    for key in sorted(versions):
        name, version = versions[key]
        # An empty pin unpins a package in buildout
        if version and key not in skip:
            sys.stdout.write('%s==%s\n' % (name, version))


if __name__ == '__main__':
    main()
