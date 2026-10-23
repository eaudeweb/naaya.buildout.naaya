"""
Post-zodbupdate ZODB migration fixes for Zope 5 / Python 3.

Run with: bin/<instance> run migrate_zodb.py

This handles all fixes that require the Zope app object (after zodbupdate
and fix_provides have been run on the raw Data.fs).

Steps:
  1. Replace broken PAS CookieAuthHelper (PlonePAS -> basic PAS)
  2. Replace the Plone PAS plugins with plain PAS and LDAPMultiPlugins ones
  3. Fix broken TextIndex -> ZCTextIndex in portal_catalog
  4. Fix relative ajax-loader.gif URL in ZODB slick-theme.css
  5. Rename bobobase_modification_time -> modification_time in glossary catalogs
  6. Delete media items whose ExtFile content no longer exists
  7. Remove contentratings storages from annotations
  8. Delete /update_logs
  9. Delete /database (MySQL apps), /fix_sortorder_default and the Acronyms portlet
  10. Convert Python 2 print statements in stored PythonScripts
  11. Number duplicate i18n:name values in stored templates
  12. Remove the 'depth' option from path queries in stored templates
  13. Replace rstk.name with rstk['name'] in stored templates and scripts
  14. Render error_value escaped with str() in stored error templates and DTML
  15. Remove .decode('utf-8') on request values in stored PythonScripts
  16. Replace test() in the root gw_macro with conditional expressions
  17. Set a 15 s op_timeout on LDAPUserFolder servers that have none
  18. Switch portal_map to OpenLayers and delete the Yahoo and Bing engines
  19. Delete the portlet_ymap (Yahoo map) portlet and its assignments

After the commit every catalog is rebuilt (one commit per catalog), then the
database is packed. '--reindex-only' skips the steps and only rebuilds and packs.
"""

import io
import os
import re
import sys
import tokenize
import transaction
from Acquisition import aq_base
from Testing.makerequest import makerequest


def _patch_normalizeargs():
    """Skip the broken Python 2 __provides__ entries instead of failing to load the object."""
    import zope.interface.declarations as zid

    def normalizeargs(sequence, output=None):
        if output is None:
            output = []
        if zid.InterfaceClass in type(sequence).__mro__ or zid.Implements in type(sequence).__mro__:
            output.append(sequence)
        else:
            try:
                for v in sequence:
                    normalizeargs(v, output)
            except TypeError:
                pass
        return output
    zid._normalizeargs = normalizeargs


_patch_normalizeargs()

app = makerequest(app)  # noqa: F821

dry_run = '--dry-run' in sys.argv
reindex_only = '--reindex-only' in sys.argv


def step_fix_pas_cookie(app):
    """Replace PlonePAS CookieAuthHelper with basic PAS CookieAuthHelper."""
    pas = getattr(app, 'acl_users', None)
    if pas is None:
        print("  No acl_users found at app root, skipping")
        return

    old = getattr(pas, 'credentials_cookie_auth', None)
    if old is None:
        print("  No credentials_cookie_auth plugin, skipping")
        return

    cls_name = type(aq_base(old)).__name__
    if cls_name == 'CookieAuthHelper':
        print("  credentials_cookie_auth is already basic CookieAuthHelper")
        return

    print(f"  Replacing {cls_name} with basic CookieAuthHelper")

    from Products.PluggableAuthService.plugins.CookieAuthHelper import (
        CookieAuthHelper,
    )
    from Products.PluggableAuthService.interfaces.plugins import (
        IChallengePlugin,
        ICredentialsResetPlugin,
        ICredentialsUpdatePlugin,
        IExtractionPlugin,
    )

    plugins = pas.plugins

    # Record which interfaces the old plugin was active for
    # (from the registry: listPlugins skips the broken Plone plugin)
    active_for = []
    for iface in [ICredentialsUpdatePlugin, ICredentialsResetPlugin,
                  IExtractionPlugin, IChallengePlugin]:
        if 'credentials_cookie_auth' in plugins._plugins.get(iface, ()):
            active_for.append(iface)

    # Preserve settings
    cookie_name = getattr(old, 'cookie_name', '__ac')
    login_path = getattr(old, 'login_path', 'login_form')

    # Replace
    pas._delObject('credentials_cookie_auth')
    new_plugin = CookieAuthHelper('credentials_cookie_auth')
    new_plugin.cookie_name = cookie_name
    new_plugin.login_path = login_path
    pas._setObject('credentials_cookie_auth', new_plugin)

    # Re-activate
    for iface in active_for:
        plugins.activatePlugin(iface, 'credentials_cookie_auth')
        print(f"    Re-activated for {iface.__name__}")

    print(f"  Done (cookie_name={cookie_name}, login_path={login_path})")


# Plone PAS plugins with a plain PAS equivalent: same id, same stored data
PAS_REPLACEMENTS = {
    'Products.PlonePAS.plugins.user.UserManager':
        ('Products.PluggableAuthService.plugins.ZODBUserManager', 'ZODBUserManager'),
    'Products.PlonePAS.plugins.group.GroupManager':
        ('Products.PluggableAuthService.plugins.ZODBGroupManager', 'ZODBGroupManager'),
    'Products.PlonePAS.plugins.role.GroupAwareRoleManager':
        ('Products.PluggableAuthService.plugins.ZODBRoleManager', 'ZODBRoleManager'),
    'Products.PloneLDAP.plugins.ldap.PloneLDAPMultiPlugin':
        ('Products.LDAPMultiPlugins.LDAPMultiPlugin', 'LDAPMultiPlugin'),
}

# Plone-only PAS plugins without data Naaya uses
PAS_REMOVALS = ('Products.PlonePAS.', 'Products.PloneLDAP.', 'borg.localrole.')


def _class_path(ob):
    """Dotted class path; Broken classes keep the original module and name."""
    cls = type(aq_base(ob))
    return '%s.%s' % (cls.__module__, cls.__name__)


def step_replace_plone_pas_plugins(app):
    """Replace the Plone PAS plugins in /acl_users with plain PAS and LDAPMultiPlugins ones."""
    import importlib
    from ZODB.broken import Broken
    pas = getattr(app, 'acl_users', None)
    if pas is None or getattr(pas, 'meta_type', '') != 'Pluggable Auth Service':
        print("  No PAS at app root, skipping")
        return
    pas = aq_base(pas)
    changed = []
    for entry in list(pas._objects):
        ob_id = entry['id']
        ob = pas.__dict__.get(ob_id)
        if ob is None:
            continue
        path = _class_path(ob)
        if path in PAS_REPLACEMENTS:
            module, name = PAS_REPLACEMENTS[path]
            cls = getattr(importlib.import_module(module), name)
            ob._p_activate()
            state = (ob.__dict__['__Broken_state__'] if isinstance(ob, Broken)
                     else ob.__getstate__())
            state = dict(state)
            state.pop('__provides__', None)  # refers to the old class
            new = cls.__new__(cls)
            new.__setstate__(state)
            pas._setOb(ob_id, new)
            entry['meta_type'] = new.meta_type
            changed.append('%s -> %s' % (ob_id, name))
        elif path.startswith(PAS_REMOVALS):
            pas._objects = tuple(o for o in pas._objects if o['id'] != ob_id)
            delattr(pas, ob_id)
            changed.append('%s removed' % ob_id)
    # Keep only PAS plugin types and the registrations their plugins provide
    registry = pas.plugins

    def pas_type(iface):
        return getattr(iface, '__module__', '').startswith('Products.PluggableAuthService.')
    for iface in list(registry._plugins.keys()):
        if not pas_type(iface):
            del registry._plugins[iface]
            continue
        ids = tuple(i for i in registry._plugins[iface]
                    if i in pas.__dict__ and iface.providedBy(pas.__dict__[i]))
        if ids != registry._plugins[iface]:
            registry._plugins[iface] = ids
    registry._plugin_types = [i for i in registry._plugin_types if pas_type(i)]
    for iface in list(registry._plugin_type_info.keys()):
        if not pas_type(iface):
            del registry._plugin_type_info[iface]
    # fix_provides.py skips the registry: its __provides__ refers to Plone PAS interfaces
    if '__provides__' in aq_base(registry).__dict__:
        del aq_base(registry).__provides__
        changed.append('plugins: __provides__ removed')
    # plone.session handles the login cookie, as on the Python 2 sites
    from Products.PluggableAuthService.interfaces.plugins import (
        IAuthenticationPlugin, ICredentialsResetPlugin,
        ICredentialsUpdatePlugin, IExtractionPlugin)
    if 'session' in pas.__dict__:
        for iface in (IExtractionPlugin, IAuthenticationPlugin,
                      ICredentialsUpdatePlugin, ICredentialsResetPlugin):
            ids = registry._plugins.get(iface, ())
            if 'session' not in ids:
                registry._plugins[iface] = ('session',) + tuple(ids)
                changed.append('session activated for %s' % iface.__name__)
    registry._p_changed = True
    for line in changed:
        print("  " + line)
    if not changed:
        print("  No Plone PAS plugins")
    # Sources moved to pasldap by an earlier version of this script
    for site_id in app.objectIds():
        site = app[site_id]
        if not hasattr(aq_base(site), 'getAuthenticationTool'):
            continue
        for src in site.getAuthenticationTool().getSources():
            if getattr(src, 'obj_path', '') == 'acl_users/pasldap':
                src.obj_path = 'acl_users/ldap-plugin/acl_users'
                print("  %s/%s: obj_path -> acl_users/ldap-plugin/acl_users" % (site_id, src.getId()))


def _fix_broken_indexes_in_catalog(catalog, catalog_path):
    """Replace broken TextIndex/TextIndexNG3 indexes with ZCTextIndex in a catalog."""
    from Products.ZCatalog.ZCatalog import ZCatalog
    if not isinstance(catalog, ZCatalog):
        return 0

    inner = catalog._catalog

    # Find broken indexes (access raw index to avoid __of__ on broken objects)
    broken = []
    for idx_name in list(inner.indexes.keys()):
        try:
            idx = inner.indexes[idx_name]
            if not hasattr(idx, '_apply_index'):
                broken.append(idx_name)
        except Exception:
            broken.append(idx_name)

    if not broken:
        return 0

    print(f"    {catalog_path}: {len(broken)} broken: {broken}")

    # Create Lexicon if missing
    if 'Lexicon' not in catalog.objectIds():
        from Products.ZCTextIndex.ZCTextIndex import PLexicon
        from Products.ZCTextIndex.Lexicon import (
            CaseNormalizer,
            Splitter,
            StopWordRemover,
        )
        lexicon = PLexicon('Lexicon', 'Default lexicon',
                           Splitter(), CaseNormalizer(), StopWordRemover())
        catalog._setObject('Lexicon', lexicon)
        print(f"    Created Lexicon in {catalog_path}")

    # Replace broken indexes
    extra = type('Extra', (), {
        'doc_attr': '',
        'index_type': 'Okapi BM25 Rank',
        'lexicon_id': 'Lexicon',
    })()

    for idx_name in broken:
        catalog.delIndex(idx_name)
        extra.doc_attr = idx_name
        catalog.addIndex(idx_name, 'ZCTextIndex', extra=extra)
        print(f"    Replaced {idx_name} -> ZCTextIndex")
    return len(broken)


def _find_all_catalogs(container, path=''):
    """Recursively find all ZCatalog instances in the ZODB."""
    from Products.ZCatalog.ZCatalog import ZCatalog
    results = []
    try:
        ids = container.objectIds()
    except Exception:
        return results
    for obj_id in ids:
        obj_path = f"{path}/{obj_id}" if path else obj_id
        try:
            obj = container[obj_id]
        except Exception:
            continue
        if isinstance(obj, ZCatalog):
            results.append((obj, obj_path))
        else:
            # Look one level deeper in site-like containers
            try:
                sub_ids = obj.objectIds()
            except Exception:
                continue
            for sub_id in sub_ids:
                sub_path = f"{obj_path}/{sub_id}"
                try:
                    sub_obj = obj[sub_id]
                except Exception:
                    continue
                if isinstance(sub_obj, ZCatalog):
                    results.append((sub_obj, sub_path))
                else:
                    # One more level for nested tools (e.g. thesaurus/catalog)
                    try:
                        for deep_id in sub_obj.objectIds():
                            deep_path = f"{sub_path}/{deep_id}"
                            try:
                                deep_obj = sub_obj[deep_id]
                                if isinstance(deep_obj, ZCatalog):
                                    results.append((deep_obj, deep_path))
                            except Exception:
                                continue
                    except Exception:
                        continue
    return results


def step_fix_catalog_indexes(app):
    """Replace broken TextIndex/TextIndexNG3 instances with ZCTextIndex in all catalogs."""
    catalogs = _find_all_catalogs(app)
    if not catalogs:
        print("  No catalogs found")
        return

    print(f"  Found {len(catalogs)} catalog(s)")
    replaced = sum(_fix_broken_indexes_in_catalog(catalog, catalog_path)
                   for catalog, catalog_path in catalogs)
    if not replaced:
        print("  No broken indexes found in any catalog")
    else:
        print(f"  Replaced {replaced} index(es); all catalogs are rebuilt after the commit")


def step_fix_slick_theme_css(app):
    """Fix relative ajax-loader.gif URL in ZODB slick-theme.css files.

    The Slick carousel theme CSS contains url("./ajax-loader.gif") which
    resolves relative to the skin scheme path (e.g.
    portal_layout/destinet/colorscheme/ajax-loader.gif) causing a 404.
    Replace with an absolute URL to /++resource++Products.Naaya/ajax-loader.gif.
    """
    fixed = 0
    for site_id in app.objectIds():
        try:
            site = app[site_id]
            layout = getattr(site, 'portal_layout', None)
            if layout is None:
                continue
        except Exception:
            continue

        for skin_id in layout.objectIds():
            try:
                skin = layout[skin_id]
                if not hasattr(skin, 'objectIds'):
                    continue
                for scheme_id in skin.objectIds():
                    scheme = skin[scheme_id]
                    if not hasattr(scheme, 'objectIds'):
                        continue
                    for obj_id in scheme.objectIds():
                        if 'slick' not in obj_id.lower():
                            continue
                        obj = scheme[obj_id]
                        content = None
                        if hasattr(obj, 'read'):
                            content = obj.read()
                        elif hasattr(obj, 'data'):
                            content = obj.data
                            if isinstance(content, bytes):
                                content = content.decode('utf-8', errors='replace')
                            else:
                                content = str(content)
                        if not content or 'ajax-loader.gif' not in content:
                            continue

                        target = '/++resource++Products.Naaya/ajax-loader.gif'
                        new_content = content.replace(
                            'url("./ajax-loader.gif")',
                            'url("%s")' % target
                        ).replace(
                            "url('./ajax-loader.gif')",
                            "url('%s')" % target
                        ).replace(
                            'url(./ajax-loader.gif)',
                            'url(%s)' % target
                        ).replace(
                            'url("/misc_/Naaya/ajax-loader.gif")',
                            'url("%s")' % target
                        ).replace(
                            "url('/misc_/Naaya/ajax-loader.gif')",
                            "url('%s')" % target
                        ).replace(
                            'url(/misc_/Naaya/ajax-loader.gif)',
                            'url(%s)' % target
                        )
                        if new_content != content:
                            path = '%s/%s/%s/%s' % (site_id, skin_id, scheme_id, obj_id)
                            # Write back
                            if hasattr(obj, 'write'):
                                obj.write(new_content)
                            elif hasattr(obj, 'update_data'):
                                obj.update_data(new_content.encode('utf-8'))
                            else:
                                obj.data = new_content.encode('utf-8')
                            obj._p_changed = True
                            print("  Fixed: %s" % path)
                            fixed += 1
            except Exception as e:
                print("  Error in %s/%s: %s" % (site_id, skin_id, e))

    if not fixed:
        print("  No slick-theme.css files needed fixing")


def step_fix_glossary_catalogs(app):
    """Rename bobobase_modification_time -> modification_time in glossary catalogs.

    In Zope 5, bobobase_modification_time() was removed and replaced by
    modification_time(). The NaayaGlossary code already uses the new name,
    but existing ZODB catalogs still have the old index and column names.
    """
    from Products.ZCatalog.ZCatalog import ZCatalog

    fixed = 0
    for site_id in app.objectIds():
        try:
            site = app[site_id]
            if not hasattr(site, 'objectIds'):
                continue
        except Exception:
            continue

        # Find glossary objects (they have a GlossaryCatalog child)
        try:
            site_ids = site.objectIds()
        except Exception:
            continue

        for obj_id in site_ids:
            try:
                obj = site[obj_id]
                catalog = getattr(obj, 'GlossaryCatalog', None)
                if catalog is None or not isinstance(catalog, ZCatalog):
                    continue
            except Exception:
                continue

            inner = catalog._catalog

            # Check if we need to fix this catalog
            has_old = 'bobobase_modification_time' in inner.indexes
            has_new = 'modification_time' in inner.indexes
            if has_old and not has_new:
                # Rename index: delete old, add new
                catalog.delIndex('bobobase_modification_time')
                catalog.addIndex('modification_time', 'FieldIndex')
                print(f"    {site_id}/{obj_id}: renamed index "
                      f"bobobase_modification_time -> modification_time")

                # Rename column if present
                if 'bobobase_modification_time' in inner.schema:
                    catalog.delColumn('bobobase_modification_time')
                    if 'modification_time' not in inner.schema:
                        catalog.addColumn('modification_time')
                    print(f"    {site_id}/{obj_id}: renamed column "
                          f"bobobase_modification_time -> modification_time")
                fixed += 1
            elif not has_old and not has_new:
                # Neither exists — add the new one
                catalog.addIndex('modification_time', 'FieldIndex')
                if 'modification_time' not in inner.schema:
                    catalog.addColumn('modification_time')
                print(f"    {site_id}/{obj_id}: added missing "
                      f"modification_time index+column")
                fixed += 1

    if not fixed:
        print("  All glossary catalogs already up to date")
    else:
        print(f"  Fixed {fixed} glossary catalog(s)")
        print("  NOTE: Run rebuildCatalog from ZMI on each glossary to reindex")


# Media items whose ExtFile content no longer exists anywhere.
DELETE_EXTFILE_MEDIA = [
    'demo-design/who-who/civil-society-ngos/olympic-truce-youth-peace-ambassadors-network/london-2012-olympic-peace-campaign/campaign-archive/olympic-peace-through-tourism-london-2012/switch-over-darfur-behind-bejing-olympic-games',
]


def step_delete_extfile_media(app):
    """Delete media items listed in DELETE_EXTFILE_MEDIA."""
    for path in DELETE_EXTFILE_MEDIA:
        try:
            media = app.unrestrictedTraverse(path)
        except (KeyError, AttributeError):
            print(f"  {path}: not found, skipping")
            continue
        base = aq_base(media)
        # Broken ExtFile children can't take delete events; remove them first.
        broken = [item['id'] for item in base._objects
                  if item.get('meta_type') == 'ExtFile']
        base._objects = tuple(item for item in base._objects
                              if item['id'] not in broken)
        for child_id in broken:
            delattr(base, child_id)
        parent = media.aq_parent
        parent.manage_delObjects([media.getId()])
        print(f"  Deleted {path}")


def step_delete_contentratings(app):
    """Remove contentratings storages from all object annotations."""
    from BTrees.OOBTree import OOBTree
    storage = app._p_jar.db().storage
    conn = app._p_jar
    removed = 0
    oid = b'\x00' * 8
    while True:
        oid, tid, data, next_oid = storage.record_iternext(oid)
        # The key lives in the annotations OOBTree record, not in its owner.
        if b'contentratings.' in data and b'OOBTree' in data[:40]:
            annotations = conn.get(oid)
            if isinstance(annotations, OOBTree):
                for key in [k for k in annotations.keys()
                            if isinstance(k, str)
                            and k.startswith('contentratings.')]:
                    del annotations[key]
                    removed += 1
        if next_oid is None:
            break
        oid = next_oid
        if removed and removed % 500 == 0:
            conn.cacheGC()
    print(f"  Removed {removed} contentratings storage(s)")


def step_delete_update_logs(app):
    """Delete /update_logs (naaya.updater logs stored as ExtFiles)."""
    if 'update_logs' not in app.objectIds():
        print("  No update_logs folder, skipping")
        return
    # Its ExtFile children are broken and can't take delete events.
    app._objects = tuple(item for item in app._objects
                         if item['id'] != 'update_logs')
    delattr(aq_base(app), 'update_logs')
    print("  Deleted /update_logs")


def step_delete_mysql_apps(app):
    """Delete /database (MySQL apps, database gone) and its leftovers."""
    # Broken ZMySQLDA/ZSQLMethods/ExternalMethod objects can't take delete events.
    for obj_id in ('database', 'fix_sortorder_default'):
        if obj_id not in app.objectIds():
            print(f"  No /{obj_id}, skipping")
            continue
        app._objects = tuple(item for item in app._objects
                             if item['id'] != obj_id)
        delattr(aq_base(app), obj_id)
        print(f"  Deleted /{obj_id}")
    site = getattr(app, 'demo-design', None)
    portlets = getattr(site, 'portal_portlets', None)
    if portlets is not None and 'portlet_879964' in portlets.objectIds():
        portlets.manage_delObjects(['portlet_879964'])
        print("  Deleted demo-design/portal_portlets/portlet_879964 (Acronyms)")


def convert_print_statements(src):
    """Rewrite Python 2 print statements as print() calls."""
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    offs = [0]
    for line in src.splitlines(keepends=True):
        offs.append(offs[-1] + len(line))

    def pos(rowcol):
        return offs[rowcol[0] - 1] + rowcol[1]

    edits = []
    at_start = True
    for i, tok in enumerate(toks):
        if tok.type in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                        tokenize.NL):
            at_start = True
            continue
        if tok.type == tokenize.COMMENT:
            continue
        if (at_start and tok.type == tokenize.NAME and tok.string == 'print'
                and toks[i + 1].string != '('):
            j = i + 1
            while toks[j].type not in (tokenize.NEWLINE, tokenize.COMMENT,
                                       tokenize.ENDMARKER):
                j += 1
            last = j - 1
            if last <= i:
                edits.append((pos(tok.start), pos(tok.end), 'print()'))
            elif toks[last].string == ',':
                # trailing comma: no newline in Python 2
                args = src[pos(toks[i + 1].start):pos(toks[last].start)]
                edits.append((pos(tok.start), pos(toks[last].end),
                              "print(%s, end=' ')" % args.rstrip()))
            else:
                args = src[pos(toks[i + 1].start):pos(toks[last].end)]
                edits.append((pos(tok.start), pos(toks[last].end),
                              'print(%s)' % args))
        at_start = False
    for start, end, new in reversed(edits):
        src = src[:start] + new + src[end:]
    return src


def _iter_records(app, marker):
    """Yield loaded objects whose pickle header contains marker."""
    conn = app._p_jar
    storage = conn.db().storage
    oid = b'\x00' * 8
    while True:
        oid, tid, data, next_oid = storage.record_iternext(oid)
        if marker in data[:150]:
            obj = conn.get(oid)
            obj._p_activate()
            yield obj
        if next_oid is None:
            break
        oid = next_oid


def step_convert_print_statements(app):
    """Convert Python 2 print statements in stored PythonScripts."""
    from Products.PythonScripts.PythonScript import PythonScript
    fixed = 0
    for script in _iter_records(app, b'PythonScript'):
        if not isinstance(script, PythonScript) or not script.errors:
            continue
        script.write(convert_print_statements(script.read()))
        status = 'still failing: %s' % script.errors[0] if script.errors else 'ok'
        print("  %s: %s" % ('/'.join(script.getPhysicalPath()), status))
        fixed += 1
    if not fixed:
        print("  No PythonScripts with errors")


def step_dedupe_i18n_names(app):
    """Number repeated i18n:name values in stored templates Chameleon rejects."""
    from Products.PageTemplates.PageTemplate import PageTemplate
    fixed = 0
    for template in _iter_records(app, b'Template'):
        if not isinstance(template, PageTemplate):
            continue
        text = getattr(template, '_text', '')
        if 'i18n:name' not in text:
            continue
        check = PageTemplate()
        check.write(text)
        check._cook()
        if not any('Duplicate translation name' in str(e)
                   for e in check._v_errors):
            continue
        seen = {}

        def number(match):
            name = match.group(1)
            seen[name] = seen.get(name, 0) + 1
            if seen[name] == 1:
                return match.group(0)
            return 'i18n:name="%s%d"' % (name, seen[name])
        template.pt_edit(re.sub(r'i18n:name="([^"]+)"', number, text),
                         template.content_type)
        print("  %s" % '/'.join(template.getPhysicalPath()))
        fixed += 1
    if not fixed:
        print("  No templates with duplicate i18n:name")


def step_remove_path_depth(app):
    """Remove the 'depth' path index option Zope 2 ignored and ZCatalog 7 rejects."""
    from Products.PageTemplates.PageTemplate import PageTemplate
    depth = re.compile(r"""\s*,\s*['"]depth['"]\s*:\s*\d+""")
    fixed = 0
    for template in _iter_records(app, b'Template'):
        if not isinstance(template, PageTemplate):
            continue
        text = getattr(template, '_text', '')
        if not depth.search(text):
            continue
        template.pt_edit(depth.sub('', text), template.content_type)
        print("  %s" % '/'.join(template.getPhysicalPath()))
        fixed += 1
    if not fixed:
        print("  No templates with a 'depth' path query")


def step_rstk_item_access(app):
    """Replace deprecated rstk.name with rstk['name'] in stored templates and scripts."""
    from Products.PageTemplates.PageTemplate import PageTemplate
    from Products.PythonScripts.PythonScript import PythonScript
    attr = re.compile(r"\brstk\.(\w+)")
    conn = app._p_jar
    storage = conn.db().storage
    fixed = 0
    oid = b'\x00' * 8
    while True:
        oid, tid, data, next_oid = storage.record_iternext(oid)
        if b'rstk.' in data:
            ob = conn.get(oid)
            if isinstance(ob, PageTemplate) and attr.search(ob._text):
                ob.pt_edit(attr.sub(r"rstk['\1']", ob._text), ob.content_type)
                print("  %s" % ob.getId())
                fixed += 1
            elif isinstance(ob, PythonScript) and attr.search(ob.body()):
                ob.ZPythonScript_edit(ob.params(),
                                      attr.sub(r"rstk['\1']", ob.body()))
                print("  %s" % ob.getId())
                fixed += 1
        if next_oid is None:
            break
        oid = next_oid
    if not fixed:
        print("  No templates or scripts with rstk.name")


def step_nocall_error_value(app):
    """Render error_value escaped with str(); Zope 5 calls HTTP exceptions as WSGI apps."""
    from OFS.DTMLMethod import DTMLMethod
    from Products.PageTemplates.PageTemplate import PageTemplate
    pt_expr = re.compile(
        r"(?:(?:structure|text)\s+)?(?:nocall:)?options/error_value\b")
    pt_new = "python:str(options.get('error_value', ''))"
    dtml_expr = re.compile(r"&dtml-error_value;|<dtml-var\s+error_value\s*>")
    dtml_new = '<dtml-var expr="str(error_value)" html_quote>'
    fixed = 0
    for template in _iter_records(app, b'Template'):
        if not isinstance(template, PageTemplate):
            continue
        text = template._text
        if not pt_expr.search(text):
            continue
        template.pt_edit(pt_expr.sub(pt_new, text), template.content_type)
        print("  %s" % '/'.join(template.getPhysicalPath()))
        fixed += 1
    for method in _iter_records(app, b'DTMLMethod'):
        if not isinstance(method, DTMLMethod):
            continue
        text = method.read()
        if not dtml_expr.search(text):
            continue
        method.manage_edit(dtml_expr.sub(dtml_new, text), method.title)
        print("  %s" % '/'.join(method.getPhysicalPath()))
        fixed += 1
    if not fixed:
        print("  No templates with options/error_value or &dtml-error_value;")


def step_request_value_decode(app):
    """Remove .decode('utf-8') on request values; they are str on Python 3."""
    from Products.PythonScripts.PythonScript import PythonScript
    expr = re.compile(r"""((?:REQUEST|request|form)(?:\.form)?\.get\([^()]*\))"""
                      r"""\.decode\(\s*['"]utf-?8['"]\s*\)""")
    fixed = 0
    for script in _iter_records(app, b'PythonScript'):
        if not isinstance(script, PythonScript) or not expr.search(script.body()):
            continue
        script.ZPythonScript_edit(script.params(), expr.sub(r'\1', script.body()))
        print("  %s" % '/'.join(script.getPhysicalPath()))
        fixed += 1
    if not fixed:
        print("  No scripts decoding request values")


def reindex_catalogs(app):
    """Rebuild every catalog from the objects it holds, one commit per catalog."""
    catalogs = _find_all_catalogs(app)
    print(f"  Found {len(catalogs)} catalog(s)")
    for catalog, catalog_path in catalogs:
        try:
            catalog.refreshCatalog(clear=1)
            transaction.get().note(f'Rebuild catalog {catalog_path}')
            transaction.commit()
            print(f"  {catalog_path}: {len(catalog)} object(s)")
        except Exception as e:
            transaction.abort()
            print(f"  {catalog_path}: FAILED, {e!r}")
        app._p_jar.cacheMinimize()


# Five (zope.tales) has no test(); root gw_macro is rendered by groupware browser views
GW_MACRO_REPLACEMENTS = [
    ("python:test(user_full_name == 'Anonymous User', None, "
     "'https://www.eionet.europa.eu/directory/user?uid='+uid)",
     "python:None if user_full_name == 'Anonymous User' else "
     "'https://www.eionet.europa.eu/directory/user?uid='+uid"),
    ("python:test(repeat['crumb'].end, 'breadcrumbitemlast', 'breadcrumbitem')",
     "python:'breadcrumbitemlast' if repeat['crumb'].end else 'breadcrumbitem'"),
]


def step_gw_macro_test(app):
    """Replace test() in the root gw_macro with conditional expressions."""
    from Products.PageTemplates.PageTemplate import PageTemplate
    template = app._getOb('gw_macro', None)
    if not isinstance(template, PageTemplate):
        print("  No root gw_macro")
        return
    text = template._text
    for old, new in GW_MACRO_REPLACEMENTS:
        text = text.replace(old, new)
    if text == template._text:
        print("  gw_macro has no test() to replace")
        return
    template.pt_edit(text, template.content_type)
    print("  gw_macro")


# A search to an LDAP server that never answers blocks the Zope thread for good
LDAP_OP_TIMEOUT = 15


def set_ldap_op_timeout(luf, timeout=LDAP_OP_TIMEOUT):
    """Set op_timeout on the servers of luf without one; return the changed servers."""
    delegate = luf._delegate
    changed = []
    for server in delegate.getServers():
        try:
            current = int(server.get('op_timeout', -1))
        except (TypeError, ValueError):
            current = -1
        # LDAPDelegate sets the timeout only when op_timeout > 0
        if current <= 0:
            server['op_timeout'] = timeout
            changed.append(server)
    if changed:
        delegate._p_changed = True
    return changed


def step_ldap_op_timeout(app):
    """Set op_timeout on the servers of every LDAPUserFolder that has none."""
    try:
        from Products.LDAPUserFolder.LDAPUserFolder import LDAPUserFolder
    except ImportError:
        print("  Products.LDAPUserFolder is not installed")
        return
    fixed = 0
    for luf in _iter_records(app, b'LDAPUserFolder'):
        if not isinstance(luf, LDAPUserFolder):
            continue
        path = '/'.join(luf.getPhysicalPath())
        for server in set_ldap_op_timeout(luf):
            print("  %s: %s://%s:%s op_timeout=%s" % (
                path, server['protocol'], server['host'], server['port'],
                server['op_timeout']))
            fixed += 1
    if not fixed:
        print("  No LDAP servers without op_timeout")


# Map engine objects whose classes were removed from GeoMapTool
REMOVED_MAP_ENGINES = ('engine_yahoo', 'engine_bing')


def step_map_engines(app):
    """Set current_engine to openlayers on every portal_map and delete the Yahoo and Bing engines."""
    fixed = 0
    for site_id in app.objectIds():
        site = app[site_id]
        portal_map = getattr(aq_base(site), 'portal_map', None)
        if getattr(portal_map, 'meta_type', None) != 'Naaya GeoMap Tool':
            continue
        portal_map = site.portal_map
        base = aq_base(portal_map)
        base._p_activate()
        changes = []
        # Remove broken engines from __dict__ without loading them
        for engine_id in REMOVED_MAP_ENGINES:
            if any(o['id'] == engine_id for o in base._objects):
                base._objects = tuple(o for o in base._objects
                                      if o['id'] != engine_id)
                changes.append('%s removed from _objects' % engine_id)
            if engine_id in base.__dict__:
                delattr(base, engine_id)
                changes.append('%s deleted' % engine_id)
        old_engine = base.__dict__.get('current_engine')
        has_openlayers = 'engine_openlayers' in base.__dict__
        portal_map.set_map_engine('openlayers')
        if old_engine != 'openlayers':
            changes.append('current_engine %r -> openlayers' % old_engine)
        if not has_openlayers:
            changes.append('engine_openlayers created')
        for line in changes:
            print("  %s/portal_map: %s" % (site_id, line))
        fixed += bool(changes)
    if not fixed:
        print("  All portal_map tools already use openlayers")


def _remove_portlet_id(container, portlet_id):
    """Remove portlet_id from a list, or from the lists in a dict; return the count."""
    removed = 0
    lists = container.values() if hasattr(container, 'values') else [container]
    for ids in lists:
        while portlet_id in ids:
            ids.remove(portlet_id)
            removed += 1
    if removed and hasattr(container, '_p_changed'):
        container._p_changed = True  # plain lists inside a PersistentMapping
    return removed


def step_delete_ymap_portlet(app, portlet_id='portlet_ymap'):
    """Delete the portlet_ymap portlet (Yahoo map) and its left/center/right assignments."""
    fixed = 0
    for site_id in app.objectIds():
        site = app[site_id]
        portlets = getattr(aq_base(site), 'portal_portlets', None)
        if portlets is None or not hasattr(portlets, 'objectIds'):
            continue
        portlets = site.portal_portlets
        if portlet_id in portlets.objectIds():
            portlets.manage_delObjects([portlet_id])
            print("  Deleted %s/portal_portlets/%s" % (site_id, portlet_id))
            fixed += 1
        layout = aq_base(portlets).__dict__.get('_portlet_layout', {})
        for (folder_path, position), items in layout.items():
            kept = [i for i in items if i['id'] != portlet_id]
            if len(kept) != len(items):
                items[:] = kept
                portlets._p_changed = True
                print("  %s: unassigned from %r at %r" % (site_id, position, folder_path))
                fixed += 1
        # Assignments kept on the site by the old portlets_manager
        for name in ('left_portlets_ids', 'center_portlets_ids', 'right_portlets_locations'):
            ids = aq_base(site).__dict__.get('_portlets_manager__' + name)
            if ids is not None and _remove_portlet_id(ids, portlet_id):
                site._p_changed = True
                print("  %s: removed from %s" % (site_id, name))
                fixed += 1
    if not fixed:
        print("  No %s portlets" % portlet_id)


def step_map_symbol_ids(app):
    """Rename map symbols whose id is a file path (naaya.content.meeting install) to the file name."""
    fixed = 0
    for site_id in app.objectIds():
        site = app[site_id]
        portal_map = getattr(aq_base(site), 'portal_map', None)
        symbols = getattr(portal_map, '_symbols_tool__symbol_collection', None)
        if not symbols:
            continue
        renamed = {}
        for old_id in [i for i in list(symbols) if '/' in i]:
            new_id = base_id = old_id.rsplit('/', 1)[1]
            n = 1
            while new_id in symbols:
                n += 1
                new_id = '%s-%d' % (base_id, n)
            symbol = symbols.pop(old_id)
            symbol.id = new_id
            symbols[new_id] = symbol
            renamed[old_id] = new_id
        if not renamed:
            continue
        portal_map._p_changed = True
        for symbol in symbols.values():
            if symbol.parent in renamed:
                symbol.parent = renamed[symbol.parent]
        catalog = site.getCatalogTool()
        for old_id, new_id in renamed.items():
            count = 0
            for brain in catalog(geo_type=old_id):
                ob = brain.getObject()
                ob.geo_type = new_id
                site.recatalogNyObject(ob)
                count += 1
            print("  %s: symbol %s -> %s, %d objects" % (site_id, old_id, new_id, count))
            fixed += 1
    if not fixed:
        print("  No map symbols with a path id")


def step_destinet_geomaptool_js(app):
    """Replace demo-design/portal_map/geomaptool.js with the copy in NaayaBundles-DESTINET."""
    try:
        import naayabundles_destinet
    except ImportError:
        print("  NaayaBundles-DESTINET is not installed")
        return
    site = app._getOb('demo-design', None)
    portal_map = getattr(aq_base(site), 'portal_map', None) if site is not None else None
    if portal_map is None or 'geomaptool.js' not in portal_map.objectIds():
        print("  No demo-design/portal_map/geomaptool.js")
        return
    path = os.path.join(os.path.dirname(naayabundles_destinet.__file__),
                        'DESTINET-demo-design.bundle', 'portal_map', 'geomaptool.js')
    with open(path, 'rb') as f:
        data = f.read()
    ob = site.portal_map._getOb('geomaptool.js')
    if bytes(ob.data) == data:
        print("  geomaptool.js already up to date")
        return
    ob.update_data(data, 'application/javascript')
    print("  Updated demo-design/portal_map/geomaptool.js from %s" % path)


DATA_IMAGE_RE = re.compile(
    r"""src=(["'])data:image/(png|jpe?g|gif|webp|svg\+xml);base64,([A-Za-z0-9+/=\s]+)\1""")
IMAGE_EXT = {'png': 'png', 'jpeg': 'jpg', 'jpg': 'jpg', 'gif': 'gif',
             'webp': 'webp', 'svg+xml': 'svg'}


def _extract_data_images(text, images, created, name):
    """Return text with base64 src images saved in images as <name>-<n>.<ext> and linked as /images/<id>."""
    import base64
    import hashlib
    from naaya.core.blobofs import manage_addBlobImage

    def replace(match):
        quote, kind, data = match.groups()
        try:
            raw = base64.b64decode(re.sub(r'\s+', '', data), validate=True)
        except ValueError:
            return match.group(0)
        digest = hashlib.sha1(raw).hexdigest()
        image_id = created.get(digest)
        if image_id is None:
            n = 1
            while '%s-%d.%s' % (name, n, IMAGE_EXT[kind]) in images.objectIds():
                n += 1
            image_id = '%s-%d.%s' % (name, n, IMAGE_EXT[kind])
            manage_addBlobImage(images, image_id, raw, content_type='image/' + kind)
            created[digest] = image_id
        return 'src=%s/images/%s%s' % (quote, image_id, quote)
    return DATA_IMAGE_RE.sub(replace, text)


def _image_name(ob):
    """Image name base from the object id without leading numbering (1., 10.2-), else from the title."""
    numbering = r'^(?:\d{1,3}[\W_]+)+'
    name = re.sub(numbering, '', ob.getId())
    if not name or name.isdigit():
        title = ob.title_or_id() if hasattr(ob, 'title_or_id') else ''
        name = re.sub(r'[^a-z0-9]+', '-', str(title).lower()).strip('-')
        name = re.sub(numbering, '', name)
    if not name or name.isdigit():
        return 'image'
    return name


def step_extract_data_images(app):
    """Move base64 images from content text to the site images folder (links /images/<id>, for sites at the domain root)."""
    fixed = 0
    for site_id in app.objectIds():
        site = app[site_id]
        if getattr(aq_base(site), 'portal_catalog', None) is None:
            continue
        images = site._getOb('images', None)
        if images is None:
            continue
        created, objects = {}, 0
        # paths first: recataloging while iterating the results skips objects
        paths = [brain.getPath() for brain in site.portal_catalog(path='/' + site_id)]
        for path in paths:
            ob = app.unrestrictedTraverse(path, None)
            if ob is None:
                continue
            base = aq_base(ob)
            # Saving a large blob makes a savepoint, which can ghost this object:
            # compute the new values first, then write them on the reloaded object
            prop_updates, attr_updates = [], []
            for name, langs in list(getattr(base, '_local_properties', {}).items()):
                for lang, value in list(langs.items()):
                    text = value[0] if isinstance(value, tuple) else value
                    if not isinstance(text, str) or 'data:image/' not in text:
                        continue
                    new = _extract_data_images(text, images, created, _image_name(ob))
                    if new != text:
                        new = (new,) + value[1:] if isinstance(value, tuple) else new
                        prop_updates.append((name, lang, new))
            base._p_activate()  # a ghost has an empty __dict__
            for name, text in list(base.__dict__.items()):
                if isinstance(text, str) and 'data:image/' in text:
                    new = _extract_data_images(text, images, created, _image_name(ob))
                    if new != text:
                        attr_updates.append((name, new))
            if prop_updates or attr_updates:
                for name, lang, value in prop_updates:
                    base._local_properties[name][lang] = value
                for name, value in attr_updates:
                    setattr(base, name, value)
                base._p_changed = True
                site.recatalogNyObject(ob)
                objects += 1
                if objects % 20 == 0:
                    transaction.savepoint(True)
        if objects:
            print("  %s: %d objects, %d images added to /images" % (site_id, objects, len(created)))
            fixed += 1
    if not fixed:
        print("  No base64 images in content")


SHH_HEADER = re.compile(
    r'<(?:[a-z]+:block|span|div)\b[^>]*?(?:tal:)?(?:replace|content)='
    r'"structure (?:here|context)/standard_html_header"[^>]*?/>')
SHH_FOOTER = re.compile(
    r'<(?:[a-z]+:block|span|div)\b[^>]*?(?:tal:)?(?:replace|content)='
    r'"structure (?:here|context)/standard_html_footer"[^>]*?/>')
SHH_DTML_HEADER = re.compile(r'<dtml-var\s+standard_html_header\s*>[ \t]*\n?')
SHH_DTML_FOOTER = re.compile(r'<dtml-var\s+standard_html_footer\s*>[ \t]*\n?')
SHH_OPEN = ('<metal:block use-macro="here/standard_template_macro">'
            '<metal:block fill-slot="body">')
SHH_CLOSE = '</metal:block></metal:block>'


def _walk_objects(container):
    """Yield (container, id, object) for every object below container."""
    for ob_id, ob in container.objectItems():
        yield container, ob_id, ob
        if (hasattr(aq_base(ob), 'objectItems')
                and getattr(ob, 'meta_type', '') != 'ZCatalog'):
            yield from _walk_objects(ob)


def step_standard_template_macro(app):
    """Replace standard_html_header/footer with standard_template_macro."""
    # DTML pages become a page template rendering the DTML (<id>_body) in the
    # macro; outside the Naaya sites the two calls are only removed
    from OFS.DTMLMethod import DTMLMethod, addDTMLMethod
    from OFS.DTMLDocument import addDTMLDocument
    from OFS.DTMLDocument import DTMLDocument
    from Products.PageTemplates.PageTemplate import PageTemplate
    from Products.PageTemplates.ZopePageTemplate import (
        manage_addPageTemplate)
    from Products.Naaya.NySite import NySite
    fixed = []
    for container, ob_id, ob in list(_walk_objects(app)):
        in_site = any(isinstance(aq_base(parent), NySite)
                      for parent in container.aq_chain)
        if isinstance(aq_base(ob), PageTemplate):
            text = ob._text
            headers, footers = SHH_HEADER.findall(text), SHH_FOOTER.findall(text)
            if not headers and not footers:
                continue
            if len(headers) != 1 or len(footers) != 1:
                print("  SKIPPED, %d headers and %d footers: %s" % (
                    len(headers), len(footers), '/'.join(ob.getPhysicalPath())))
                continue
            if in_site:
                h, f = SHH_HEADER.search(text), SHH_FOOTER.search(text)
                text = (text[:h.start()] + SHH_OPEN + text[h.end():f.start()] +
                        SHH_CLOSE + text[f.end():])
            else:
                text = SHH_FOOTER.sub('', SHH_HEADER.sub('', text))
            ob.pt_edit(text, ob.content_type)
            fixed.append(ob)
        elif isinstance(aq_base(ob), DTMLMethod):
            text = ob.read()
            if not (SHH_DTML_HEADER.search(text) or SHH_DTML_FOOTER.search(text)):
                continue
            body = SHH_DTML_FOOTER.sub('', SHH_DTML_HEADER.sub('', text))
            if not in_site:
                ob.manage_edit(body, ob.title)
                fixed.append(ob)
                continue
            body_id = ob_id + '_body'
            title = ob.title
            add = (addDTMLDocument if isinstance(aq_base(ob), DTMLDocument)
                   else addDTMLMethod)
            container._delObject(ob_id)
            add(container, body_id, title, body)
            manage_addPageTemplate(container, ob_id, title, (
                SHH_OPEN + '\n<tal:block replace="structure python:'
                'here[%r](here, request)" />\n' % body_id + SHH_CLOSE + '\n'))
            fixed.append(container._getOb(ob_id))
    for ob in fixed:
        print("  %s" % '/'.join(ob.getPhysicalPath()))
    if not fixed:
        print("  No templates with standard_html_header/footer")


# --- Run all steps ---

STEPS = [
    ("Fix PAS CookieAuthHelper", step_fix_pas_cookie),
    ("Replace the Plone PAS plugins", step_replace_plone_pas_plugins),
    ("Fix broken TextIndex -> ZCTextIndex", step_fix_catalog_indexes),
    ("Fix slick-theme.css ajax-loader.gif URL", step_fix_slick_theme_css),
    ("Fix glossary catalog bobobase_modification_time", step_fix_glossary_catalogs),
    ("Delete media items without content", step_delete_extfile_media),
    ("Delete contentratings storages", step_delete_contentratings),
    ("Delete /update_logs", step_delete_update_logs),
    ("Delete /database MySQL apps", step_delete_mysql_apps),
    ("Convert print statements in PythonScripts", step_convert_print_statements),
    ("Number duplicate i18n:name values in templates", step_dedupe_i18n_names),
    ("Remove 'depth' from path queries in templates", step_remove_path_depth),
    ("Use rstk['name'] in templates and scripts", step_rstk_item_access),
    ("Use nocall:options/error_value in error templates", step_nocall_error_value),
    ("Remove .decode('utf-8') on request values in scripts", step_request_value_decode),
    ("Replace test() in the root gw_macro", step_gw_macro_test),
    ("Set op_timeout on LDAP servers", step_ldap_op_timeout),
    ("Switch portal_map to OpenLayers, delete Yahoo/Bing engines", step_map_engines),
    ("Delete the portlet_ymap portlet", step_delete_ymap_portlet),
    ("Rename map symbols with a file path id", step_map_symbol_ids),
    ("Update the destinet portal_map/geomaptool.js", step_destinet_geomaptool_js),
    ("Move base64 images from content to /images", step_extract_data_images),
    ("Use standard_template_macro instead of standard_html_header/footer",
     step_standard_template_macro),
]

print("=" * 60)
print("Naaya ZODB migration fixes for Zope 5 / Python 3")
if dry_run:
    print("DRY RUN - no changes will be committed")
print("=" * 60)

for i, (label, func) in enumerate([] if reindex_only else STEPS, 1):
    print("\n[%d/%d] %s" % (i, len(STEPS), label))
    func(app)

if dry_run:
    print("\nDRY RUN - aborting transaction")
    transaction.abort()
else:
    if not reindex_only:
        print("\nCommitting transaction...")
        transaction.commit()
    print("\nRebuilding catalogs...")
    reindex_catalogs(app)
    print("Packing (drops unreachable objects)...")
    app._p_jar.db().pack()
    print("Done.")
