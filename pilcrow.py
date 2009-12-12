#!/usr/bin/env python
"""
Static site generator.

Copyright (c) 2009 Liam Cooke
Licensed under the terms of the MIT license.

"""
import codecs, commands, optparse, os, re, shutil, sys, time
from collections import defaultdict
from datetime import datetime
from itertools import izip
from os import path

import dateutil.parser
import yaml
from mako.exceptions import MakoException
from mako.lookup import TemplateLookup
from markdown import markdown

CONFIG_FILE = 'site.yml'
FILES_ACTIONS = {
    '.less': lambda s, d: run_or_die('lessc %s %s' % (s, d)),
}
site = yaml.load(r"""
    clean_urls: no
    content_extensions: [text, markdown, mkdn, md]
    dirs:
        content: content
        deploy: deploy
        files: files
        templates: templates
    files_exclude: "(^\\.|~$)"
    files_include: "^\\.htaccess$"
    files_rename:
        .less: .css
""")

alphanum = lambda s: re.sub('[^A-Za-z0-9]', '', s)
filemtime = lambda f: datetime.fromtimestamp(os.fstat(f.fileno()).st_mtime)
identity = lambda o: o
is_str = lambda o: isinstance(o, basestring)
timestamp = lambda dt: dt and int(time.mktime(dt.timetuple())) or 0

def die(*msg):
    sys.stderr.write(' '.join(str(m) for m in msg) + '\n')
    sys.exit(1)

def run_or_die(cmd):
    status, output = commands.getstatusoutput(cmd)
    if status > 0: die(output)

norm_key = lambda s: re.sub('[- ]+', '_', s.lower())
norm_time = lambda s: s and dateutil.parser.parse(str(s), fuzzy=True) or None
def norm_tags(obj):
    tags = is_str(obj) and obj.split(',' in obj and ',' or None) or obj
    return tuple(filter(bool, (alphanum(tag) for tag in tags)))

def join_url(*parts, **kwargs):
    ext = (kwargs.get('ext', 1) and not site['clean_urls']) and '.html' or ''
    return re.sub('//+', '/', '/'.join(str(s) for s in parts if s)) + ext
site['join_url'] = join_url

def mkdir(d):
    try: os.mkdir(d)
    except OSError: pass

def neighbours(iterable):
    "1..4 -> (None,1,2), (1,2,3), (2,3,4), (3,4,None)"
    L = list(iterable)
    a = [None] + L[:-1]
    b = L[1:] + [None]
    return izip(a, L, b)

class Page(dict):
    sortkey_origin = lambda self: (timestamp(self.date), self.id)
    sortkey_posted = lambda self: (timestamp(self.posted or self.date), self.id)

    def __init__(self, id, attrs={}, **kwargs):
        dict.__init__(self, {
            'date': None,
            'posted': None,
            'id': str(id),
            'title': '',
            'template': '',
        })
        self.update(attrs)
        self.update(kwargs)

    def __getattr__(self, name):
        return self[name]

    @property
    def url(self):
        id = self.id
        return join_url(site['root'], id != 'index' and id)

class ContentPage(Page):
    NORM = {
        'date': norm_time, 'posted': norm_time,
        'tags': norm_tags, 'category': norm_tags,
        'summary': markdown,
    }
    SUMMARY = re.compile('(<summary>)(.*?)(</summary>)', re.DOTALL)

    def __init__(self, fp):
        id = path.splitext(path.basename(fp.name))[0]
        Page.__init__(self, id, modified=filemtime(fp))
        data = fp.read().split('\n\n', 1)
        head = yaml.load(data.pop(0))
        body = data and data.pop() or ''

        for key, val in head.items():
            key = norm_key(key)
            self[key] = self.NORM.get(key, identity)(val)
        if self.date:
            self.update({
                'id': join_url(self.date.year, id, ext=False),
                'template': self.template or 'entry',
                'month_name': self.date.strftime('%B'),
                'prevpost': None,
                'nextpost': None,
            })

        def _summary(m):
            summary = m.group(2).strip()
            self['summary'] = markdown(summary)
            return summary
        self['content'] = markdown(self.SUMMARY.sub(_summary, body).strip())

class ArchivePage(Page):

    def __init__(self, entries, year, month=0):
        id = join_url(year, month and '%02d' % month, ext=False)
        Page.__init__(self, id, {
            'entries': entries,
            'year': year,
            'month': month,
            'template': 'archive_%s' % (month and 'month' or 'year'),
            'title': month and datetime(year, month, 1).strftime('%B %Y') or year,
        })

class PageManager:

    def __init__(self):
        self.pages = {}
        tdir = site['dirs']['templates']
        self.lookup = TemplateLookup(directories=[tdir], input_encoding='utf-8')

    def add(self, page):
        if page.id in self.pages:
            die('duplicate page id: %s' % page.id)
        self.pages[page.id] = page

    def __getitem__(self, id):
        return self.pages[id]

    def all(self, sortby_origin=False):
        sortkey = sortby_origin and Page.sortkey_origin or Page.sortkey_posted
        return sorted(self.pages.values(), key=sortkey)

    def __iter__(self):
        return iter(self.pages.values())

    def render(self):
        for page in self:
            t = page.template or site['default_template']
            template = self.lookup.get_template('%s.html' % t)
            print '%14s : /%s' % (t, page.id)

            vars = dict(site, **page)
            if vars['title']:
                vars['head_title'] = vars['title_format'] % vars
            try:
                html = template.render_unicode(**vars).strip()
                fname = path.join(site['dirs']['deploy'], page.id) + '.html'
                with open(fname, 'w') as f:
                    f.write(html.encode('utf-8'))
            except NameError:
                die('template error: undefined variable in', template.filename)

def build(site_path, clean=False):
    try: os.chdir(site_path)
    except OSError: die('invalid path:', site_path)
    if not path.exists(CONFIG_FILE):
        die('%s not found' % CONFIG_FILE)

    with open(CONFIG_FILE) as f:
        for k, v in yaml.load(f).items():
            if type(v) is dict:
                site[norm_key(k)].update(v)
            else:
                site[norm_key(k)] = v

    base_path = path.realpath(os.curdir)
    deploy_path = path.realpath(site['dirs']['deploy'])
    if clean:
        shutil.rmtree(deploy_path, ignore_errors=True)
        mkdir(deploy_path)

    os.chdir(site['dirs']['files'])
    excludes, includes = re.compile(site['files_exclude']), re.compile(site['files_include'])
    for root, _, files in os.walk(os.curdir):
        mkdir(path.normpath(path.join(deploy_path, root)))
        for fname in files:
            if excludes.match(fname) and not includes.match(fname):
                continue
            src, dest = path.join(root, fname), path.join(deploy_path, root, fname)
            ext = path.splitext(fname)[1]
            if ext in site['files_rename']:
                dest = path.splitext(dest)[0] + site['files_rename'][ext]
            if path.isfile(dest) and path.getmtime(src) <= path.getmtime(dest):
                continue
            FILES_ACTIONS.get(ext, shutil.copy2)(src, dest)
            print '%s => %s' % (path.relpath(src, base_path), path.relpath(dest, base_path))
    os.chdir(base_path)

    pages, years = PageManager(), defaultdict(list)
    for root, _, files in os.walk(site['dirs']['content']):
        exts = ['.%s' % ext for ext in site['content_extensions']]
        for file in filter(lambda f: path.splitext(f)[1] in exts, files):
            with codecs.open(path.join(root, file), 'r', encoding='utf-8') as fp:
                page = ContentPage(fp)
                pages.add(page)
                if page.date:
                    years[page.date.year].append(page)

    for year, posts in sorted(years.items()):
        posts = sorted(posts, key=Page.sortkey_origin)
        pages.add(ArchivePage(posts, year))
        for prevpost, post, nextpost in neighbours(posts):
            post['prevpost'], post['nextpost'] = prevpost, nextpost

    dirs = filter(bool, [os.path.dirname(p.id) for p in pages])
    for d in sorted(set(dirs)):
        mkdir(os.path.join(deploy_path, d))

    def select(limit=None, dated=True, chrono=False, sortby_origin=None):
        if sortby_origin is None: sortby_origin = bool(chrono)
        results = pages.all(sortby_origin)
        if not chrono: results.reverse()
        if dated: results = [page for page in results if page.date]
        return tuple(results)[:limit]

    site.update({
        'get': lambda id: pages[str(id)],
        'pages': select,
        'domain': site['domain'].rstrip('/'),
        'root': '/' + site.get('root', '').lstrip('/'),
        'head_title': site.get('site_title', ''),
        'years': sorted(years.keys()),
        'default_template': site.get('default_template', 'page'),
    })
    try: pages.render()
    except MakoException as e: die('template error:', e)

if __name__ == '__main__':
    parser = optparse.OptionParser()
    parser.add_option('-x', '--clean', action='store_true', default=False)
    options, args = parser.parse_args()
    build(args and args[0] or '.', clean=options.clean)
