# -*- coding: utf-8 -*-
"""
      Sphinx rst2pdf builder extension
      ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

      Usage:
      1. In conf.py add 'rst2pdf.pdfbuilder' element to 'extensions' list:
         extensions = ['rst2pdf.pdfbuilder']
      2. Modify your Makefile or run it with:
         $ sphinx-build -d_build/doctrees -bpdf . _build/pdf

    :copyright: Copyright 2009 Roberto Alsina, Wojtek Walczak
    :license: BSD, see LICENSE for details.
"""

from copy import copy
from io import BytesIO
import logging
import os
import os.path
import re
import sys
import time
from urllib.parse import urlunparse

from docutils import writers
from docutils import nodes
from docutils.transforms.parts import Contents
from docutils.io import FileOutput
import docutils.core
import jinja2
from pygments.lexers import guess_lexer
import sphinx
from sphinx import addnodes
from sphinx.builders import Builder
from sphinx.environment.adapters.indexentries import IndexEntries
from sphinx.locale import _
from sphinx.transforms import SphinxTransform
from sphinx.util.console import darkgreen, red  # type: ignore
from sphinx.util import SEP

import rst2pdf
from rst2pdf import createpdf
from rst2pdf.directives import code_block
from rst2pdf.log import log
from rst2pdf.languages import get_language_available

if sphinx.__version__ >= '2.1':
    from sphinx.errors import NoUri
else:
    from sphinx.environment import NoUri


class PDFBuilder(Builder):
    name = 'pdf'
    out_suffix = '.pdf'
    supported_image_types = ['image/svg+xml', 'image/png', 'image/gif', 'image/jpeg']

    def init(self):
        self.docnames = []
        self.document_data = []
        self.sphinx_logger = sphinx.util.logging.getLogger(__name__)

    def write(self, *ignored):

        self.init_document_data()

        if self.config.pdf_verbosity > 1:
            log.setLevel(logging.DEBUG)
        elif self.config.pdf_verbosity > 0:
            log.setLevel(logging.INFO)

        for entry in self.document_data:
            try:
                docname, targetname, title, author = entry[:4]
                # Custom options per document
                if len(entry) > 4 and isinstance(entry[4], dict):
                    opts = entry[4]
                else:
                    opts = {}
                self.sphinx_logger.info("processing " + targetname + "... ")
                self.opts = opts

                class dummy:
                    extensions = self.config.pdf_extensions

                createpdf.add_extensions(dummy())

                self.page_template = opts.get(
                    'pdf_page_template', self.config.pdf_page_template
                )

                docwriter = PDFWriter(
                    self,
                    stylesheets=opts.get(
                        'pdf_stylesheets', self.config.pdf_stylesheets
                    ),
                    language=opts.get('pdf_language', self.config.pdf_language),
                    breaklevel=opts.get('pdf_break_level', self.config.pdf_break_level),
                    breakside=opts.get('pdf_breakside', self.config.pdf_breakside),
                    fontpath=opts.get('pdf_font_path', self.config.pdf_font_path),
                    fitmode=opts.get('pdf_fit_mode', self.config.pdf_fit_mode),
                    compressed=opts.get('pdf_compressed', self.config.pdf_compressed),
                    inline_footnotes=opts.get(
                        'pdf_inline_footnotes', self.config.pdf_inline_footnotes
                    ),
                    splittables=opts.get(
                        'pdf_splittables', self.config.pdf_splittables
                    ),
                    repeat_table_rows=opts.get(
                        'pdf_repeat_table_rows', self.config.pdf_repeat_table_rows
                    ),
                    default_dpi=opts.get(
                        'pdf_default_dpi', self.config.pdf_default_dpi
                    ),
                    page_template=self.page_template,
                    invariant=opts.get('pdf_invariant', self.config.pdf_invariant),
                    real_footnotes=opts.get(
                        'pdf_real_footnotes', self.config.pdf_real_footnotes
                    ),
                    use_toc=opts.get('pdf_use_toc', self.config.pdf_use_toc),
                    toc_depth=opts.get('pdf_toc_depth', self.config.pdf_toc_depth),
                    use_coverpage=opts.get(
                        'pdf_use_coverpage', self.config.pdf_use_coverpage
                    ),
                    use_numbered_links=opts.get(
                        'pdf_use_numbered_links', self.config.pdf_use_numbered_links
                    ),
                    fit_background_mode=opts.get(
                        'pdf_fit_background_mode', self.config.pdf_fit_background_mode
                    ),
                    baseurl=opts.get('pdf_baseurl', self.config.pdf_baseurl),
                    section_header_depth=opts.get(
                        'section_header_depth', self.config.section_header_depth
                    ),
                    smartquotes=str(
                        opts.get('pdf_smartquotes', self.config.pdf_smartquotes)
                    ),
                    srcdir=self.srcdir,
                    style_path=opts.get('pdf_style_path', self.config.pdf_style_path),
                    config=self.config,
                )

                tgt_file = os.path.join(self.outdir, targetname + self.out_suffix)
                destination = FileOutput(
                    destination=open(tgt_file, 'wb'), encoding='utf-8'
                )
                doctree = self.assemble_doctree(
                    docname,
                    title,
                    author,
                    appendices=opts.get('pdf_appendices', self.config.pdf_appendices)
                    or [],
                )
                doctree.settings.author = author
                doctree.settings.title = title
                self.sphinx_logger.info("done")
                self.sphinx_logger.info("writing " + targetname + "... ")
                docwriter.write(doctree, destination)
                self.sphinx_logger.info("done")
            except Exception:
                log.exception('Failed to build doc')
                self.sphinx_logger.info(red("FAILED"))

    def init_document_data(self):
        preliminary_document_data = map(list, self.config.pdf_documents)
        if not preliminary_document_data:
            self.sphinx_logger.warning(
                'no "pdf_documents" config value found; no documents ' 'will be written'
            )
            return
        # assign subdirs to titles
        self.titles = []
        for entry in preliminary_document_data:
            docname = entry[0]
            if docname not in self.env.all_docs:
                self.sphinx_logger.warning(
                    '"pdf_documents" config value references unknown '
                    'document %s' % docname
                )
                continue
            self.document_data.append(entry)
            if docname.endswith(SEP + 'index'):
                docname = docname[:-5]
            self.titles.append((docname, entry[2]))

    def assemble_doctree(self, docname, title, author, appendices):

        # FIXME: use the new inline_all_trees from Sphinx.
        # check how the LaTeX builder does it.

        self.docnames = set([docname])
        self.sphinx_logger.info(darkgreen(docname) + " ")

        def process_tree(docname, tree):
            tree = tree.deepcopy()
            for toctreenode in tree.traverse(addnodes.toctree):
                newnodes = []
                includefiles = map(str, toctreenode['includefiles'])
                for includefile in includefiles:
                    try:
                        self.sphinx_logger.info(darkgreen(includefile) + " ")
                        subtree = process_tree(
                            includefile, self.env.get_doctree(includefile)
                        )
                        self.docnames.add(includefile)
                    except Exception:
                        self.sphinx_logger.warning(
                            '%s: toctree contains ref to nonexisting file %r'
                            % (docname, includefile)
                        )
                    else:
                        sof = addnodes.start_of_file(docname=includefile)
                        sof.children = subtree.children
                        newnodes.append(sof)
                toctreenode.parent.replace(toctreenode, newnodes)
            return tree

        tree = self.env.get_doctree(docname)
        tree = process_tree(docname, tree)

        self.docutils_languages = {}
        if self.config.language:
            self.docutils_languages[self.config.language] = get_language_available(
                self.config.language
            )[2]

        if self.opts.get('pdf_use_index', self.config.pdf_use_index):
            # Add index at the end of the document

            # This is a hack. create_index creates an index from
            # ALL the documents data, not just this one.
            # So, we preserve a copy, use just what we need, then
            # restore it.
            if hasattr(self.env, 'domains') and "index" in self.env.domains:
                # Sphinx 2.4.0+ stores the index entries in self.env.domains["index"]
                t = copy(self.env.domains["index"].data['entries'])
                try:
                    self.env.domains["index"].data["entries"] = {
                        docname: self.env.domains["index"].data['entries'][
                            docname + '-gen'
                        ]
                    }
                except KeyError:
                    self.env.domains["index"].data["entries"] = {}
                    for dname in self.docnames:
                        self.env.domains["index"].data["entries"][dname] = t.get(
                            dname, []
                        )

                genindex = IndexEntries(self.env).create_index(self)
                self.env.domains["index"].data['entries'] = t
            elif hasattr(self.env, 'indexentries'):
                # Sphinx 2.3.1 or lower stores the index entries in self.env.indexentries
                t = copy(self.env.indexentries)
                try:
                    self.env.indexentries = {
                        docname: self.env.indexentries[docname + '-gen']
                    }
                except KeyError:
                    self.env.indexentries = {}
                    for dname in self.docnames:
                        self.env.indexentries[dname] = t.get(dname, [])

                genindex = IndexEntries(self.env).create_index(self)
                self.env.indexentries = t
            # EOH (End Of Hack)

            if genindex:  # No point in creating empty indexes
                index_nodes = genindex_nodes(genindex)
                tree.append(nodes.raw(text='OddPageBreak twoColumn', format='pdf'))
                tree.append(index_nodes)

        # This is stolen from the HTML builder's prepare_writing function
        self.domain_indices = []
        # html_domain_indices can be False/True or a list of index names
        indices_config = self.config.pdf_domain_indices
        if indices_config and hasattr(self.env, 'domains'):
            for domain in self.env.domains.values():
                for indexcls in domain.indices:
                    indexname = '%s-%s' % (domain.name, indexcls.name)
                    if isinstance(indices_config, list):
                        if indexname not in indices_config:
                            continue
                    # deprecated config value
                    if indexname == 'py-modindex' and not self.config.pdf_use_modindex:
                        continue
                    content, collapse = indexcls(domain).generate()
                    if content:
                        self.domain_indices.append(
                            (indexname, indexcls, content, collapse)
                        )

        # self.domain_indices contains a list of indices to generate, like
        # this:
        # [('py-modindex',
        #    <class 'sphinx.domains.python.PythonModuleIndex'>,
        #   [(u'p', [[u'parrot', 0, 'test', u'module-parrot', 'Unix, Windows',
        #   '', 'Analyze and reanimate dead parrots.']])], True)]

        # Now this in the HTML builder is passed onto write_domain_indices.
        # We handle it right here

        for indexname, indexcls, content, collapse in self.domain_indices:
            # In HTML this is handled with a Jinja template, domainindex.html
            # We have to generate docutils stuff right here in the same way.
            self.sphinx_logger.info(' ' + indexname)

            output = ['DUMMY', '=====', '', '.. _modindex:\n\n']
            t = indexcls.localname
            t += '\n' + '=' * len(t) + '\n'
            output.append(t)

            for letter, entries in content:
                output.append('.. cssclass:: heading4\n\n%s\n\n' % letter)
                for (
                    name,
                    grouptype,
                    page,
                    anchor,
                    extra,
                    qualifier,
                    description,
                ) in entries:
                    if qualifier:
                        q = '[%s]' % qualifier
                    else:
                        q = ''

                    if extra:
                        e = '(%s)' % extra
                    else:
                        e = ''
                    output.append('`%s <#%s>`_ %s %s' % (name, anchor, e, q))
                    output.append('    %s' % description)
                output.append('')

            dt = docutils.core.publish_doctree('\n'.join(output))[1:]
            dt.insert(0, nodes.raw(text='OddPageBreak twoColumn', format='pdf'))
            tree.extend(dt)

        if appendices:
            tree.append(
                nodes.raw(text='OddPageBreak %s' % self.page_template, format='pdf')
            )
            self.sphinx_logger.info('')
            self.sphinx_logger.info('adding appendixes...')
            for docname in appendices:
                self.sphinx_logger.info(darkgreen(docname) + " ")
                appendix = self.env.get_doctree(docname)
                appendix['docname'] = docname
                tree.append(appendix)
            self.sphinx_logger.info('done')

        # Replace Sphinx's HighlightLanguageTransform with our own for sphinx version between 1.8.0 & less than 2.0.0 as
        # Sphinx's HighlightLanguageTransform breaks linenothreshold setting in the highlight directive (See issue #721)
        # This code can be removed when we drop support for Python 2
        if sphinx.__version__ > '1.7.9' and sphinx.__version__ < '2.0.0':
            for i in range(len(self.env.app.registry.post_transforms)):
                if (
                    self.env.app.registry.post_transforms[i].__name__
                    == 'HighlightLanguageTransform'
                ):
                    self.env.app.registry.post_transforms[
                        i
                    ] = HighlightLanguageTransform
                    break

        self.sphinx_logger.info("resolving references...")
        self.env.resolve_references(tree, docname, self)

        for pendingnode in tree.traverse(addnodes.pending_xref):
            # This needs work, need to keep track of all targets
            # so I don't replace and create hanging refs, which
            # crash
            if (
                pendingnode.get('reftarget', None) == 'genindex'
                and self.config.pdf_use_index
            ):
                pendingnode.replace_self(
                    nodes.reference(
                        text=pendingnode.astext(), refuri=pendingnode['reftarget']
                    )
                )
            # FIXME: probably need to handle dangling links to domain-specific indexes
            else:
                # FIXME: This is from the LaTeX builder and I still don't understand it
                # well, and doesn't seem to work

                # resolve :ref:s to distant tex files -- we can't add a cross-reference,
                # but append the document name
                docname = pendingnode['refdocname']
                sectname = pendingnode['refsectname']
                newnodes = [nodes.emphasis(sectname, sectname)]
                for subdir, title in self.titles:
                    if docname.startswith(subdir):
                        newnodes.append(nodes.Text(_(' (in '), _(' (in ')))
                        newnodes.append(nodes.emphasis(title, title))
                        newnodes.append(nodes.Text(')', ')'))
                        break
                else:
                    pass
                pendingnode.replace_self(newnodes)
            # else:
            # pass
        return tree

    def get_target_uri(self, docname, typ=None):
        # print 'GTU',docname,typ
        # FIXME: production lists are not supported yet!
        if typ == 'token':
            # token references are always inside production lists and must be
            # replaced by \token{} in LaTeX
            return '@token'
        if docname not in self.docnames:

            # It can be a 'main' document:
            for doc in self.document_data:
                if doc[0] == docname:
                    return "pdf:" + doc[1] + '.pdf'
            # It can be in some other document's toctree
            for indexname, toctree in self.env.toctree_includes.items():
                if docname in toctree:
                    for doc in self.document_data:
                        if doc[0] == indexname:
                            return "pdf:" + doc[1] + '.pdf'
            # No idea
            raise NoUri
        else:  # Local link
            return ""

    def get_relative_uri(self, from_, to, typ=None):
        # ignore source path
        return self.get_target_uri(to, typ)

    def get_outdated_docs(self):
        for docname in self.env.found_docs:
            if docname not in self.env.all_docs:
                yield docname
                continue
            targetname = os.path.join(self.outdir, docname + self.out_suffix)
            try:
                targetmtime = os.path.getmtime(targetname)
            except Exception:
                targetmtime = 0
            try:
                srcmtime = os.path.getmtime(self.env.doc2path(docname))
                if srcmtime > targetmtime:
                    yield docname
            except EnvironmentError:
                # source doesn't exist anymore
                pass


def genindex_nodes(genindexentries):
    indexlabel = _('Index')
    indexunder = '=' * len(indexlabel)
    output = ['DUMMY', '=====', '.. _genindex:\n\n', indexlabel, indexunder, '']

    for key, entries in genindexentries:
        output.append('.. cssclass:: heading4\n\n%s\n\n' % key)  # initial
        for entryname, entryvalue in entries:
            links, subitems = entryvalue[0:2]
            if links:
                output.append('`%s <#%s>`_' % (entryname, nodes.make_id(links[0][1])))
                for i, link in enumerate(links[1:]):
                    output[-1] += ' `[%s] <#%s>`_ ' % (i + 1, nodes.make_id(link[1]))
                output.append('')
            else:
                output.append(entryname)
            if subitems:
                for subentryname, subentrylinks in subitems:
                    if subentrylinks:
                        output.append(
                            '    `%s <%s>`_' % (subentryname, subentrylinks[0])
                        )
                        for i, link in enumerate(subentrylinks[1:]):
                            output[-1] += ' `[%s] <%s>`_ ' % (i + 1, link)
                        output.append('')
                    else:
                        output.append(subentryname)
                        output.append('')

    doctree = docutils.core.publish_doctree('\n'.join(output))
    return doctree[1]


class PDFContents(Contents):

    # Mostly copied from Docutils' Contents transformation

    def build_contents(self, node, level=0):
        level += 1
        sections = []
        # Replaced this with the for below to make it work for Sphinx
        # trees.

        # sections = [sect for sect in node if isinstance(sect, nodes.section)]
        for sect in node:
            if isinstance(sect, nodes.compound):
                for sect2 in sect:
                    if isinstance(sect2, addnodes.start_of_file):
                        for sect3 in sect2:
                            if isinstance(sect3, nodes.section):
                                sections.append(sect3)
            elif isinstance(sect, nodes.section):
                sections.append(sect)
        entries = []
        # FIXME: depth should be taken from :maxdepth: (Issue 320)
        depth = self.toc_depth
        for section in sections:
            title = section[0]
            auto = title.get('auto')  # May be set by SectNum.
            entrytext = self.copy_and_filter(title)
            reference = nodes.reference('', '', refid=section['ids'][0], *entrytext)
            ref_id = self.document.set_id(reference)
            entry = nodes.paragraph('', '', reference)
            item = nodes.list_item('', entry)
            if (
                self.backlinks in ('entry', 'top')
                and title.next_node(nodes.reference) is None
            ):
                if self.backlinks == 'entry':
                    title['refid'] = ref_id
                elif self.backlinks == 'top':
                    title['refid'] = self.toc_id
            if level < depth:
                subsects = self.build_contents(section, level)
                item += subsects
            entries.append(item)
        if entries:
            contents = nodes.bullet_list('', *entries)
            if auto:
                contents['classes'].append('auto-toc')
            return contents
        else:
            return []


class PDFWriter(writers.Writer):
    def __init__(
        self,
        builder,
        stylesheets,
        language,
        breaklevel=0,
        breakside='any',
        fontpath=[],
        fitmode='shrink',
        compressed=False,
        inline_footnotes=False,
        splittables=True,
        srcdir='.',
        default_dpi=300,
        page_template='decoratedPage',
        invariant=False,
        real_footnotes=False,
        use_toc=True,
        use_coverpage=True,
        toc_depth=9999,
        use_numbered_links=False,
        fit_background_mode="scale",
        section_header_depth=2,
        baseurl=urlunparse(['file', os.getcwd() + os.sep, '', '', '', '']),
        style_path=None,
        repeat_table_rows=False,
        smartquotes='0',
        config={},
    ):
        writers.Writer.__init__(self)
        self.builder = builder
        self.output = ''
        self.stylesheets = stylesheets
        self.__language = language
        self.breaklevel = int(breaklevel)
        self.breakside = breakside
        self.fontpath = fontpath
        self.fitmode = fitmode
        self.compressed = compressed
        self.inline_footnotes = inline_footnotes
        self.splittables = splittables
        self.highlightlang = builder.config.highlight_language
        self.srcdir = srcdir
        self.config = config
        self.default_dpi = default_dpi
        self.page_template = page_template
        self.invariant = invariant
        self.real_footnotes = real_footnotes
        self.use_toc = use_toc
        self.use_coverpage = use_coverpage
        self.toc_depth = toc_depth
        self.use_numbered_links = use_numbered_links
        self.fit_background_mode = fit_background_mode
        self.section_header_depth = section_header_depth
        self.repeat_table_rows = repeat_table_rows
        self.smartquotes = smartquotes
        self.baseurl = baseurl
        if hasattr(sys, 'frozen'):
            self.PATH = os.path.abspath(os.path.dirname(sys.executable))
        else:
            self.PATH = os.path.abspath(os.path.dirname(__file__))
        if style_path:
            self.style_path = style_path
        else:
            self.style_path = [self.srcdir]

    supported = 'pdf'
    config_section = 'pdf writer'
    config_section_dependencies = ('writers',)

    def translate(self):
        visitor = PDFTranslator(self.document, self.builder)
        self.document.walkabout(visitor)
        lang = self.config.language or 'en'
        langmod = get_language_available(lang)[2]
        self.docutils_languages = {lang: langmod}

        # Generate Contents topic manually
        if self.use_toc:
            contents = nodes.topic(classes=['contents'])
            contents += nodes.title('')
            contents[0] += nodes.Text(langmod.labels['contents'])
            contents['ids'] = ['Contents']
            pending = nodes.topic()
            contents.append(pending)
            pending.details = {}
            self.document.insert(
                0, nodes.raw(text='SetPageCounter 1 arabic', format='pdf')
            )
            self.document.insert(
                0, nodes.raw(text='OddPageBreak %s' % self.page_template, format='pdf')
            )
            self.document.insert(0, contents)
            self.document.insert(
                0, nodes.raw(text='SetPageCounter 1 lowerroman', format='pdf')
            )
            contTrans = PDFContents(self.document)
            contTrans.toc_depth = self.toc_depth
            contTrans.startnode = pending
            contTrans.apply()

        if self.use_coverpage:
            # Generate cover page

            # FIXME: duplicate from createpdf, refactor!
            # Add the Sphinx template paths
            def add_template_path(path):
                return os.path.join(self.srcdir, path)

            jinja_env = jinja2.Environment(
                loader=jinja2.FileSystemLoader(
                    [
                        self.srcdir,
                        os.path.expanduser('~/.rst2pdf'),
                        os.path.join(self.PATH, 'templates'),
                    ]
                    + list(map(add_template_path, self.config.templates_path))
                ),
                autoescape=jinja2.select_autoescape(['html', 'xml']),
            )

            try:
                template = jinja_env.get_template(self.config.pdf_cover_template)
            except jinja2.TemplateNotFound:
                log.error(
                    "Can't find cover template %s, using default"
                    % self.config.pdf_cover_template
                )
                template = jinja_env.get_template('sphinxcover.tmpl')

            # This is what's used in the python docs because
            # Latex does a manual linebreak. This sucks.
            authors = self.document.settings.author.split('\\')

            # Honour the "today" config setting
            if self.config.today:
                date = self.config.today
            else:
                date = time.strftime(self.config.today_fmt or _('%B %d, %Y'))

            # Feed data to the template, get restructured text.
            cover_text = template.render(
                title=self.document.settings.title or visitor.elements['title'],
                subtitle='%s %s' % (self.config.subtitle_prefix, self.config.version),
                authors=authors,
                date=date,
            )

            cover_tree = docutils.core.publish_doctree(cover_text)
            self.document.insert(0, cover_tree)

        sio = BytesIO()

        if self.invariant:
            createpdf.patch_PDFDate()
            createpdf.patch_digester()

        createpdf.RstToPdf(
            sphinx=True,
            stylesheets=self.stylesheets,
            language=self.__language,
            breaklevel=self.breaklevel,
            breakside=self.breakside,
            fit_mode=self.fitmode,
            font_path=self.fontpath,
            inline_footnotes=self.inline_footnotes,
            highlightlang=self.highlightlang,
            splittables=self.splittables,
            style_path=self.style_path,
            repeat_table_rows=self.repeat_table_rows,
            basedir=self.srcdir,
            def_dpi=self.default_dpi,
            real_footnotes=self.real_footnotes,
            numbered_links=self.use_numbered_links,
            background_fit_mode=self.fit_background_mode,
            baseurl=self.baseurl,
            section_header_depth=self.section_header_depth,
            toc_depth=self.toc_depth,
            smarty=self.smartquotes,
        ).createPdf(doctree=self.document, output=sio, compressed=self.compressed)
        self.output = sio.getvalue()

    def supports(self, format):
        """This writer supports all format-specific elements."""
        return 1


class PDFTranslator(nodes.SparseNodeVisitor):
    def __init__(self, document, builder):
        nodes.NodeVisitor.__init__(self, document)
        self.builder = builder
        self.footnotestack = []
        self.curfilestack = []
        self.highlightlinenothreshold = 999999
        self.top_sectionlevel = 1
        self.footnotecounter = 1
        self.curfile = None
        self.footnotedict = {}
        self.this_is_the_title = True
        self.in_title = 0
        self.elements = {
            'title': document.settings.title,
        }
        self.highlightlang = builder.config.highlight_language

    def visit_document(self, node):
        self.curfilestack.append(node.get('docname', ''))
        self.footnotestack.append('')

    def visit_start_of_file(self, node):
        self.curfilestack.append(node['docname'])
        self.footnotestack.append(node['docname'])

    def depart_start_of_file(self, node):
        self.footnotestack.pop()
        self.curfilestack.pop()

    def visit_highlightlang(self, node):
        self.highlightlang = node['lang']
        self.highlightlinenothreshold = node['linenothreshold']
        raise nodes.SkipNode

    def visit_versionmodified(self, node):
        replacement = nodes.paragraph()
        replacement.extend(node.children)
        node.parent.replace(node, replacement)

    def depart_versionmodified(self, node):
        pass

    def visit_literal_block(self, node):
        if 'code' in node['classes']:  # Probably a processed code-block
            pass
        else:
            lang = lang_for_block(
                node.astext(), node.get('language', self.highlightlang)
            )
            content = node.astext().splitlines()
            if len(content) > self.highlightlinenothreshold or node.get(
                'linenos', False
            ):
                options = {'linenos': True}
            else:
                options = {}

            # FIXME: make tab width configurable
            content = [c.replace('\t', '        ') for c in content]
            replacement = nodes.literal_block()
            replacement.children = code_block.code_block_directive(
                name=None,
                arguments=[lang],
                options=options,
                content=content,
                lineno=False,
                content_offset=None,
                block_text=None,
                state=None,
                state_machine=None,
            )
            node.parent.replace(node, replacement)

    def visit_footnote(self, node):
        node['backrefs'] = [
            '%s_%s' % (self.footnotestack[-1], x) for x in node['backrefs']
        ]
        node['ids'] = ['%s_%s' % (self.footnotestack[-1], x) for x in node['ids']]
        node.children[0][0] = nodes.Text(str(self.footnotecounter))
        for id in node['backrefs']:
            try:
                fnr = self.footnotedict[id]
            except KeyError:
                pass
            else:
                fnr.children[0] = nodes.Text(str(self.footnotecounter))
        self.footnotedict[node['ids'][0]] = node
        self.footnotecounter += 1

    def visit_footnote_reference(self, node):
        node['ids'] = ['%s_%s' % (self.footnotestack[-1], x) for x in node['ids']]
        node['refid'] = '%s_%s' % (self.footnotestack[-1], node['refid'])
        self.footnotedict[node['ids'][0]] = node
        try:
            footnote = self.footnotedict[node['refid']]
        except KeyError:
            pass
        else:
            node.children[0] = nodes.Text(footnote.children[0][0])

    def visit_desc_annotation(self, node):
        pass

    def depart_desc_annotation(self, node):
        pass

    # This is for graphviz support
    def visit_graphviz(self, node):
        # Not neat, but I need to send self to my handlers
        node['builder'] = self

    def visit_Aanode(self, node):
        pass

    def depart_Aanode(self, node):
        pass

    def visit_productionlist(self, node):
        replacement = nodes.literal_block(classes=["code"])
        names = []
        for production in node:
            names.append(production['tokenname'])
        maxlen = max(len(name) for name in names)
        for production in node:
            if production['tokenname']:
                lastname = production['tokenname'].ljust(maxlen)
                n = nodes.strong()
                n += nodes.Text(lastname)
                replacement += n
                replacement += nodes.Text(' ::= ')
            else:
                replacement += nodes.Text('%s     ' % (' ' * len(lastname)))
            production.walkabout(self)
            replacement.children.extend(production.children)
            replacement += nodes.Text('\n')
        node.parent.replace(node, replacement)
        raise nodes.SkipNode

    def depart_productionlist(self, node):
        pass

    def visit_production(self, node):
        pass

    def depart_production(self, node):
        pass

    def visit_OddEvenNode(self, node):
        pass

    def depart_OddEvenNode(self, node):
        pass


class HighlightLanguageTransform(SphinxTransform):
    """
    This is a copy of Sphinx's HighlightLanguageTransform for use with Sphinx versions between 1.8.0 & less than 2.0.0
    as the Sphinx version of this class breaks the linenothreshold setting in the highlight directive (See issue #721).
    This code can be removed when we drop support for Python 2

    Apply highlight_language to all literal_block nodes.

    This refers both :confval:`highlight_language` setting and
    :rst:dir:`highlightlang` directive.

    After processing, this overridden transform DOES NOT REMOVE ``highlightlang`` node from doctree in order to allow
    pdfbuilder's visit_highlightlang to work as before.
    """

    default_priority = 400

    def apply(self):
        from sphinx.transforms.post_transforms.code import HighlightLanguageVisitor

        visitor = HighlightLanguageVisitor(
            self.document, self.config.highlight_language
        )
        self.document.walkabout(visitor)


# This is copied from sphinx.highlighting
def lang_for_block(source, lang):
    if lang in ('py', 'python'):
        if source.startswith('>>>'):
            # interactive session
            return 'pycon'
        else:
            # maybe Python -- try parsing it
            if try_parse(source):
                return 'python'
            else:  # Guess
                return lang_for_block(source, 'guess')
    elif lang in ('python3', 'py3') and source.startswith('>>>'):
        # for py3, recognize interactive sessions, but do not try parsing...
        return 'pycon3'
    elif lang == 'guess':
        try:
            # return 'python'
            lexer = guess_lexer(source)
            return lexer.aliases[0]
        except Exception:
            return None
    else:
        return lang


def try_parse(src):
    # Make sure it ends in a newline
    src += '\n'

    # Replace "..." by a mark which is also a valid python expression
    # (Note, the highlighter gets the original source, this is only done
    #  to allow "..." in code and still highlight it as Python code.)
    mark = "__highlighting__ellipsis__"
    src = src.replace("...", mark)

    # lines beginning with "..." are probably placeholders for suite
    src = re.sub(r"(?m)^(\s*)" + mark + "(.)", r"\1" + mark + r"# \2", src)

    if not isinstance(src, bytes):
        # Non-ASCII chars will only occur in string literals
        # and comments.  If we wanted to give them to the parser
        # correctly, we'd have to find out the correct source
        # encoding.  Since it may not even be given in a snippet,
        # just replace all non-ASCII characters.
        src = src.encode('ascii', 'replace')

    return True


def setup(app):
    app.add_builder(PDFBuilder)
    # PDF options
    app.add_config_value('pdf_documents', [], None)
    app.add_config_value('pdf_stylesheets', ['sphinx'], None)
    app.add_config_value('pdf_style_path', None, None)
    app.add_config_value('pdf_compressed', False, None)
    app.add_config_value('pdf_font_path', [], None)
    app.add_config_value('pdf_language', 'en_US', None)
    app.add_config_value('pdf_fit_mode', '', None),
    app.add_config_value('pdf_break_level', 0, None)
    app.add_config_value('pdf_inline_footnotes', True, None)
    app.add_config_value('pdf_verbosity', 0, None)
    app.add_config_value('pdf_use_index', True, None)
    app.add_config_value('pdf_domain_indices', True, None)
    app.add_config_value('pdf_use_modindex', True, None)
    app.add_config_value('pdf_use_coverpage', True, None)
    app.add_config_value('pdf_cover_template', 'sphinxcover.tmpl', None)
    app.add_config_value('pdf_appendices', [], None)
    app.add_config_value('pdf_splittables', True, None)
    app.add_config_value('pdf_repeat_table_rows', False, None)
    app.add_config_value('pdf_breakside', 'odd', None)
    app.add_config_value('pdf_default_dpi', 300, None)
    app.add_config_value('pdf_extensions', [], None)
    app.add_config_value('pdf_page_template', 'decoratedPage', None)
    app.add_config_value('pdf_invariant', False, None)
    app.add_config_value('pdf_real_footnotes', False, None)
    app.add_config_value('pdf_use_toc', True, None)
    app.add_config_value('pdf_toc_depth', 9999, None)
    app.add_config_value('pdf_use_numbered_links', False, None)
    app.add_config_value('pdf_fit_background_mode', "scale", None)
    app.add_config_value('pdf_smartquotes', 0, None)
    app.add_config_value('section_header_depth', 2, None)
    app.add_config_value(
        'pdf_baseurl', urlunparse(['file', os.getcwd() + os.sep, '', '', '', '']), None
    )
    app.add_config_value('subtitle_prefix', 'version', None)

    if len(app.config.pdf_documents) == 0:
        project_doc = app.config.project + ' Documentation'
        app.config.pdf_documents.append(
            (
                app.config.master_doc,
                app.config.project,
                project_doc,
                app.config.copyright,
                'manual',
            )
        )

    return {
        'version': rst2pdf.version,
        'parallel_read_safe': True,
        'parallel_write_safe': False,
    }
