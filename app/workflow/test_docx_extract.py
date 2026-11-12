"""Synthetic, dependency-free native DOCX extraction checks."""
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from workflow.docx_extract import extract_docx

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def paragraph(text):
    return '<w:p><w:r><w:t xml:space="preserve">' + text + '</w:t></w:r></w:p>'


def cell(text, props=""):
    return '<w:tc><w:tcPr>' + props + '</w:tcPr>' + paragraph(text) + '</w:tc>'


def image(rel="image1"):
    return f'<w:r><w:drawing><a:blip r:embed="{rel}"/></w:drawing></w:r>'


class DocxExtractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.docx"
        self.out = self.root / "out"

    def make_docx(self, body, rels="", styles=None, parts=None):
        document = (f'<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}">'
                    '<w:body>' + body + '</w:body></w:document>')
        with zipfile.ZipFile(self.source, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", document)
            if rels:
                archive.writestr("word/_rels/document.xml.rels",
                                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                 + rels + '</Relationships>')
            if styles:
                archive.writestr("word/styles.xml", f'<w:styles xmlns:w="{W}">' + styles + '</w:styles>')
            for name, data in (parts or {}).items():
                archive.writestr(name, data)

    def run_extract(self):
        digest = hashlib.sha256(self.source.read_bytes()).digest()
        result = extract_docx(self.source, self.out)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), digest)
        self.assertEqual(result["method"], "docx-native")
        return result

    def test_order_table_image_and_untouched_source(self):
        table = '<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid><w:tr>' + cell("left") + cell("right") + '</w:tr></w:tbl>'
        body = paragraph(" Before  colour ") + table + '<w:p><w:r><w:t>look</w:t></w:r>' + image() + '<w:r><w:t>end</w:t></w:r></w:p>'
        self.make_docx(body, f'<Relationship Id="image1" Type="{R}/image" Target="media/a%20b.png"/>',
                       parts={"word/media/a b.png": b"PNG-content", "word/header1.xml": paragraph("HEADER OMITTED")})
        result = self.run_extract()
        self.assertEqual([b["type"] for b in result["blocks"]], ["paragraph", "table", "paragraph"])
        self.assertEqual(result["text"], " Before  colour \n[TABLE 1]\nleft | right\n[/TABLE]\nlook[IMG a_b.png]end")
        self.assertEqual(result["media"][0]["block_index"], 2)
        self.assertEqual((self.out / "media" / "a_b.png").read_bytes(), b"PNG-content")
        self.assertEqual(list(self.out.iterdir()), [self.out / "media"])
        self.assertNotIn("HEADER", result["text"])

    def test_true_merged_grid_and_nested_cell_order(self):
        nested = '<w:tbl><w:tr>' + cell("nested") + '</w:tr></w:tbl>'
        table = ('<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid>'
                 '<w:tr>' + cell("merged", '<w:gridSpan w:val="2"/><w:vMerge w:val="restart"/>')
                 + cell("top") + '</w:tr><w:tr>'
                 + cell("", '<w:gridSpan w:val="2"/><w:vMerge/>')
                 + '<w:tc>' + paragraph("before") + nested + paragraph("after") + '</w:tc>'
                 '</w:tr></w:tbl>')
        self.make_docx(table)
        result = self.run_extract()
        outer = result["tables"][0]
        self.assertEqual(outer["grid_columns"], 3)
        self.assertEqual(outer["grid"][0], ["merged", "", "top"])
        self.assertEqual(outer["grid"][1][:2], ["", ""])
        self.assertEqual(outer["rows"][0]["cells"][0]["colspan"], 2)
        self.assertEqual(outer["rows"][0]["cells"][0]["rowspan"], 2)
        self.assertEqual(outer["rows"][1]["cells"][0]["merge_origin"], [0, 0])
        blocks = outer["rows"][1]["cells"][1]["blocks"]
        self.assertEqual([b["type"] for b in blocks], ["paragraph", "table", "paragraph"])
        self.assertEqual(blocks[0]["text"], "before")
        self.assertEqual(blocks[1]["grid"], [["nested"]])
        self.assertEqual(blocks[2]["text"], "after")

    def test_underlines_scripts_styles_tabs_breaks_and_raw_spelling(self):
        styles = ('<w:docDefaults><w:rPrDefault><w:rPr><w:u/></w:rPr></w:rPrDefault></w:docDefaults>'
                  '<w:style w:type="paragraph" w:styleId="Base"><w:rPr><w:b/></w:rPr></w:style>'
                  '<w:style w:type="paragraph" w:styleId="Child"><w:basedOn w:val="Base"/></w:style>'
                  '<w:style w:type="character" w:styleId="Sup"><w:rPr><w:vertAlign w:val="superscript"/></w:rPr></w:style>'
                  '<w:style w:type="character" w:styleId="Derived"><w:basedOn w:val="Sup"/></w:style>')
        body = ('<w:p><w:pPr><w:pStyle w:val="Child"/><w:pageBreakBefore/></w:pPr>'
                '<w:r><w:rPr><w:rStyle w:val="Derived"/></w:rPr><w:t>colour</w:t></w:r>'
                '<w:r><w:rPr><w:b w:val="0"/><w:u w:val="none"/><w:vertAlign w:val="subscript"/></w:rPr><w:t>2</w:t></w:r>'
                '<w:r><w:rPr><w:b w:val="0"/><w:u w:val="none"/></w:rPr><w:tab/><w:t> raw </w:t><w:br/><w:t>next</w:t><w:br w:type="page"/><w:lastRenderedPageBreak/></w:r>'
                '</w:p>')
        self.make_docx(body, styles=styles)
        result = self.run_extract()
        self.assertEqual(result["text"], '**<u><sup>colour</sup></u>**<sub>2</sub>\t raw \nnext\n')
        self.assertEqual(result["blocks"][0]["page_breaks"], [
            {"type": "page", "source": "pageBreakBefore"},
            {"type": "page", "source": "explicit"},
            {"type": "page", "source": "lastRenderedPageBreak"}])
        self.assertNotIn("pages", result)

    def test_missing_and_external_relations_are_offline(self):
        body = ('<w:p><w:hyperlink r:id="link"><w:r><w:t>external visible text</w:t></w:r></w:hyperlink>'
                + image("missing") + image("external") + '</w:p>')
        rels = (f'<Relationship Id="external" Type="{R}/image" Target="https://example.invalid/image.png" TargetMode="External"/>'
                f'<Relationship Id="link" Type="{R}/hyperlink" Target="https://example.invalid/" TargetMode="External"/>')
        self.make_docx(body, rels)
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            result = self.run_extract()
        self.assertEqual(result["text"], "external visible text[IMG unresolved][IMG unresolved]")
        self.assertFalse(result["media"])
        self.assertEqual(len(result["warnings"]), 2)
        self.assertFalse(self.out.exists())

    def test_unsafe_targets_unique_names_and_existing_files(self):
        self.make_docx('<w:p>' + image("bad") + image("one") + image("two") + image("one") + '</w:p>',
                       f'<Relationship Id="bad" Type="{R}/image" Target="../../outside.png"/>'
                       f'<Relationship Id="one" Type="{R}/image" Target="media/a/x.png"/>'
                       f'<Relationship Id="two" Type="{R}/image" Target="media/b/x.png"/>',
                       parts={"word/media/a/x.png": b"one", "word/media/b/x.png": b"two", "../../outside.png": b"unsafe"})
        directory = self.out / "media"
        directory.mkdir(parents=True)
        (directory / "x.png").write_bytes(b"existing")
        result = self.run_extract()
        self.assertEqual([m["name"] for m in result["media"]], ["x_2.png", "x_3.png", "x_2.png"])
        self.assertEqual((directory / "x.png").read_bytes(), b"existing")
        self.assertEqual((directory / "x_2.png").read_bytes(), b"one")
        self.assertFalse((self.root / "outside.png").exists())
        self.assertTrue(any("unsafe" in w for w in result["warnings"]))

    def test_floating_textbox_symbol_and_private_use_warnings(self):
        body = ('<w:p><w:r><w:rPr><w:rFonts w:ascii="Wingdings"/></w:rPr><w:t>\ue001</w:t>'
                '<w:drawing xmlns:wp="urn:test"><wp:anchor><w:txbxContent>'
                + paragraph("textbox raw") + '</w:txbxContent></wp:anchor></w:drawing></w:r></w:p>')
        self.make_docx(body)
        result = self.run_extract()
        self.assertEqual(result["text"], "\ue001textbox raw")
        self.assertTrue(any("Floating" in w for w in result["warnings"]))
        self.assertTrue(any("Textbox" in w for w in result["warnings"]))
        self.assertTrue(any("Private-use" in w for w in result["warnings"]))
        self.assertTrue(any("Symbol font" in w for w in result["warnings"]))

    def test_symbol_font_with_defined_unicode_char_does_not_warn(self):
        # Segoe UI Symbol 承载的是明确的 Unicode 符号 ★(U+2605)，不是字体映射歧义，
        # 不应告警、不应把整卷判为提取不确定（05 批次装饰星号误报的回归）。
        body = ('<w:p><w:r><w:rPr><w:rFonts w:ascii="Segoe UI Symbol"/></w:rPr>'
                '<w:t>\u2605</w:t></w:r></w:p>')
        self.make_docx(body)
        result = self.run_extract()
        self.assertEqual(result["text"], "\u2605")
        self.assertFalse(any("Symbol font" in w for w in result["warnings"]), result["warnings"])

    def test_symbol_font_with_ascii_letter_still_warns(self):
        # Wingdings 把 ASCII 字母按字形表改写成图符，仍必须告警（字面可能不一致）。
        body = ('<w:p><w:r><w:rPr><w:rFonts w:ascii="Wingdings"/></w:rPr>'
                '<w:t>l</w:t></w:r></w:p>')
        self.make_docx(body)
        result = self.run_extract()
        self.assertTrue(any("Symbol font" in w for w in result["warnings"]), result["warnings"])

    def test_sparse_table_grid_does_not_invent_cell_text(self):
        body = ('<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid>'
                '<w:tr><w:trPr><w:gridBefore w:val="1"/><w:gridAfter w:val="1"/></w:trPr>'
                + cell("middle") + '</w:tr></w:tbl>')
        self.make_docx(body)
        table = self.run_extract()["tables"][0]
        self.assertEqual(table["grid"], [["", "middle", ""]])
        self.assertEqual(len(table["rows"][0]["cells"]), 1)
        self.assertEqual(table["rows"][0]["cells"][0]["column"], 1)

    def test_resource_limits_and_entities_rejected(self):
        self.make_docx(paragraph("safe"))
        with patch("workflow.docx_extract.MAX_TOTAL", 1):
            with self.assertRaisesRegex(ValueError, "resource"):
                self.run_extract()
        with zipfile.ZipFile(self.source, "w") as archive:
            archive.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY a "boom">]><x/>')
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            self.run_extract()
        self.assertFalse(self.out.exists())

    def test_direct_formatting_and_image_inside_wrapped_body_table(self):
        body = (paragraph("") + '<w:sdt><w:sdtContent><w:tbl><w:tr><w:tc><w:p>'
                '<w:r><w:rPr><w:u/><w:b/><w:vertAlign w:val="superscript"/></w:rPr>'
                '<w:t> x </w:t></w:r>' + image() + '</w:p></w:tc></w:tr></w:tbl>'
                '</w:sdtContent></w:sdt>')
        self.make_docx(body, f'<Relationship Id="image1" Type="{R}/image" Target="media/x.png"/>',
                       parts={"word/media/x.png": b"image"})
        result = self.run_extract()
        self.assertEqual(result["media"][0]["block_index"], 1)
        self.assertEqual(result["blocks"][1]["block_index"], 1)
        self.assertEqual(result["tables"][0]["grid"], [["**<u><sup> x </sup></u>**[IMG x.png]"]])

    def test_missing_image_part_and_malformed_url_are_warnings(self):
        self.make_docx('<w:p>' + image("missingpart") + image("malformed") + '</w:p>',
                       f'<Relationship Id="missingpart" Type="{R}/image" Target="media/missing.png"/>'
                       f'<Relationship Id="malformed" Type="{R}/image" Target="http://[bad"/>')
        result = self.run_extract()
        self.assertEqual(result["text"], "[IMG unresolved][IMG unresolved]")
        self.assertEqual(len(result["warnings"]), 2)
        self.assertFalse(self.out.exists())

    def test_media_symlink_is_rejected(self):
        self.make_docx('<w:p>' + image() + '</w:p>',
                       f'<Relationship Id="image1" Type="{R}/image" Target="media/x.png"/>',
                       parts={"word/media/x.png": b"image"})
        self.out.mkdir()
        (self.out / "media").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.run_extract()
        self.assertFalse((self.root / "x.png").exists())


if __name__ == "__main__":
    unittest.main()
