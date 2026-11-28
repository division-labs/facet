#!/usr/bin/env python3
"""Visual source extractor for Facet Stage 0V (@Scribe Vision/PDF OCR).

Stages high-resolution PNG plates (200 DPI for PDFs, >=1100 px width for SVG
diagrams, and figure images from local or approved remote HTML documents) into
``<cwd>/.pipeline_scratch/ocr_staging/`` using zero mandatory third-party Python
dependencies.

Grounding References:
  - Google DeepMind Gemma 4 Vision Capabilities
    (https://ai.google.dev/gemma/docs/capabilities/vision/image):
    High-detail document/math OCR targets the 560-1120 visual soft-token
    budget (~200 DPI anti-aliased page plates).
  - Apple Developer Documentation — PDFKit & CoreGraphics PDF Stream Parsing
    (https://developer.apple.com/documentation/pdfkit/pdfpage,
    https://developer.apple.com/documentation/coregraphics/cgpdfcontentstream,
    https://developer.apple.com/documentation/coregraphics/cgpdfscanner):
    Uses ``PDFKit`` for 200 DPI page rasterization and ``CoreGraphics``
    ``CGPDFScanner`` / ``CGPDFContentStream`` to decode PDF operator streams,
    ``/ToUnicode`` + Type-1 ``/Encoding`` font tables, baseline script shifts,
    and vector fraction-bar rules with zero third-party Python packages.
  - Facet Approved Research Sources Governance
    (``.github/hooks/approved_sources.json``): All remote HTTP(S) targets and
    embedded ``<img src="...">`` URLs are validated via
    ``pipeline_guards.load_approved_research_sources()`` and
    ``is_approved_research_url()``.

Function Map:
  - Section 1: Embedded Swift `CGPDFScanner`, `PDFKit` & SVG Renderers (`DEFAULT_DPI`, `SWIFT_PDF_INSPECTOR`, `SWIFT_PDF_RENDERER`, `SWIFT_SVG_RENDERER`)
  - Section 2: Domain Governance Bridge, Binary Raster Validation & SVG Transcoding (`validate_and_inspect_raster`, `transcode_legacy_raster_to_png`, `render_svg_to_png`)
  - Section 3: PDF Stream Inspection, `SOURCE_TEXT.md` Normalization & Lossless Plate Rendering (`inspect_pdf_document`, `normalize_pdf_page_markdown`, `persist_verbatim_source_text`, `render_pdf_to_pngs`)
  - Section 4: Approved HTML/Remote Figure Extraction & Safe Redirect Handling (`_ApprovedRedirectHandler`, `_stage_image_bytes`, `extract_html_sources`)
  - Section 5: CLI Argument Parser & Visual Staging Entrypoint (`build_arg_parser`, `main`)

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import argparse
import base64
import html.parser
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

HttpFetcher = Callable[[str, Path, Optional[object]], Tuple[bytes, str]]


# ============================================================================
# Section 1: Embedded Swift `CGPDFScanner`, `PDFKit` & SVG Renderers
# ============================================================================

DEFAULT_DPI: int = 200
DEFAULT_MAX_PAGES: int = 32
DEFAULT_SVG_WIDTH: int = 1100
MAX_HTTP_REDIRECTS: int = 5
MIN_ICON_MAX_DIM: int = 64
MIN_ICON_AREA: int = 1024
MAX_DOWNLOAD_BYTES: int = 32 * 1024 * 1024  # 32 MiB ceiling per source asset
USER_AGENT: str = "Facet-VisualSourceExtractor/1.0 (+https://github.com/division-labs/facet)"


SWIFT_PDF_INSPECTOR: str = r"""
import Foundation
import PDFKit
import CoreGraphics

let glyphMap: [String: String] = [
    "summationdisplay": "∑", "summationtext": "∑",
    "productdisplay": "∏", "producttext": "∏",
    "integraldisplay": "∫", "integraltext": "∫",
    "uniondisplay": "⋃", "uniontext": "⋃",
    "intersectiondisplay": "⋂", "intersectiontext": "⋂",
    "parenleftBig": "(", "parenleftbig": "(", "parenleftbigg": "(", "parenleftBigg": "(", "parenleft": "(",
    "parenrightBig": ")", "parenrightbig": ")", "parenrightbigg": ")", "parenrightBigg": ")", "parenright": ")",
    "braceleftBig": "{", "braceleftbig": "{", "braceleftbigg": "{", "braceleftBigg": "{", "braceleft": "{",
    "bracerightBig": "}", "bracerightbig": "}", "bracerightbigg": "}", "bracerightBigg": "}", "braceright": "}",
    "bracketleftBig": "[", "bracketleftbig": "[", "bracketleft": "[",
    "bracketrightBig": "]", "bracketrightbig": "]", "bracketright": "]",
    "vextendsingle": "|", "bar": "|", "bardbl": "‖",
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ", "lambda": "λ",
    "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ",
    "upsilon": "υ", "phi": "ϕ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "partialdiff": "∂", "nabla": "∇", "infinity": "∞", "element": "∈", "notelement": "∉",
    "universal": "∀", "existential": "∃", "minus": "−", "plusminus": "±", "multiply": "×",
    "periodcentered": "·", "radical": "√", "radicalbt": "√",
    "lessequal": "≤", "greaterequal": "≥", "notequal": "≠", "equivalence": "≡", "approxequal": "≈",
    "arrowright": "→", "arrowleft": "←", "arrowboth": "↔", "mapsto": "↦",
    "intercal": "⊤", "perp": "⊥", "asteriskmath": "*", "lscript": "ℓ",
    "negationslash": "/", "slash": "/", "backslash": "\\", "dagger": "†", "endash": "–", "emdash": "—",
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "equal": "=", "plus": "+", "comma": ",", "period": ".", "colon": ":", "semicolon": ";",
    "less": "<", "greater": ">", "exclam": "!", "fi": "fi", "fl": "fl", "ff": "ff", "ffi": "ffi", "ffl": "ffl"
]

func parseFontMap(_ fontDict: CGPDFDictionaryRef) -> [Int: String] {
    var cmap: [Int: String] = [:]
    var tuStream: CGPDFStreamRef?
    if CGPDFDictionaryGetStream(fontDict, "ToUnicode", &tuStream), let s = tuStream {
        var fmt = CGPDFDataFormat.raw
        if let cfData = CGPDFStreamCopyData(s, &fmt) as Data?,
           let txt = String(data: cfData, encoding: .isoLatin1),
           let pairRE = try? NSRegularExpression(pattern: "<([0-9A-Fa-f]{2})>\\s*<([0-9A-Fa-f]{4,8})>"),
           let rangeRE = try? NSRegularExpression(pattern: "<([0-9A-Fa-f]{2})>\\s*<([0-9A-Fa-f]{2})>\\s*<([0-9A-Fa-f]{4})>") {
            let ns = txt as NSString
            for m in pairRE.matches(in: txt, range: NSRange(location: 0, length: ns.length)) {
                if let code = Int(ns.substring(with: m.range(at: 1)), radix: 16) {
                    let hx = ns.substring(with: m.range(at: 2))
                    var scalars: [UnicodeScalar] = []
                    var hasPUA = false
                    var idx = hx.startIndex
                    while idx < hx.endIndex {
                        let nxt = hx.index(idx, offsetBy: 4, limitedBy: hx.endIndex) ?? hx.endIndex
                        if let u = UInt32(hx[idx..<nxt], radix: 16), let sc = UnicodeScalar(u) {
                            if u >= 0xE000 && u <= 0xF8FF { hasPUA = true }
                            scalars.append(sc)
                        }
                        idx = nxt
                    }
                    if !hasPUA && !scalars.isEmpty {
                        cmap[code] = String(String.UnicodeScalarView(scalars))
                    }
                }
            }
            for m in rangeRE.matches(in: txt, range: NSRange(location: 0, length: ns.length)) {
                if let c0 = Int(ns.substring(with: m.range(at: 1)), radix: 16),
                   let c1 = Int(ns.substring(with: m.range(at: 2)), radix: 16),
                   let u0 = Int(ns.substring(with: m.range(at: 3)), radix: 16), c0 <= c1 {
                    for c in c0...c1 {
                        let u = u0 + (c - c0)
                        if !(u >= 0xE000 && u <= 0xF8FF), let sc = UnicodeScalar(u) {
                            cmap[c] = String(sc)
                        }
                    }
                }
            }
        }
    }
    var encObj: CGPDFObjectRef?
    if CGPDFDictionaryGetObject(fontDict, "Encoding", &encObj), let eo = encObj {
        var encDict: CGPDFDictionaryRef?
        if CGPDFObjectGetValue(eo, .dictionary, &encDict), let ed = encDict {
            var diffArr: CGPDFArrayRef?
            if CGPDFDictionaryGetArray(ed, "Differences", &diffArr), let da = diffArr {
                var curCode = 0
                let cnt = CGPDFArrayGetCount(da)
                for i in 0..<cnt {
                    var intVal: CGPDFInteger = 0
                    var namePtr: UnsafePointer<CChar>?
                    if CGPDFArrayGetInteger(da, i, &intVal) {
                        curCode = Int(intVal)
                    } else if CGPDFArrayGetName(da, i, &namePtr), let np = namePtr {
                        let gname = String(cString: np)
                        if cmap[curCode] == nil {
                            if let mapped = glyphMap[gname] { cmap[curCode] = mapped }
                            else if gname.count == 1 { cmap[curCode] = gname }
                        }
                        curCode += 1
                    }
                }
            }
        }
    }
    var fdDict: CGPDFDictionaryRef?
    if CGPDFDictionaryGetDictionary(fontDict, "FontDescriptor", &fdDict), let fd = fdDict {
        for ffKey in ["FontFile", "FontFile2", "FontFile3"] {
            var ffStream: CGPDFStreamRef?
            if CGPDFDictionaryGetStream(fd, ffKey, &ffStream), let ffs = ffStream {
                var fmt = CGPDFDataFormat.raw
                if let cfData = CGPDFStreamCopyData(ffs, &fmt) as Data?,
                   let txt = String(data: cfData, encoding: .isoLatin1),
                   let dupRE = try? NSRegularExpression(pattern: "dup\\s+(\\d+)\\s+/([A-Za-z0-9_.]+)\\s+put") {
                    let ns = txt as NSString
                    for m in dupRE.matches(in: txt, range: NSRange(location: 0, length: ns.length)) {
                        if let code = Int(ns.substring(with: m.range(at: 1))) {
                            let gname = ns.substring(with: m.range(at: 2))
                            if cmap[code] == nil {
                                if let mapped = glyphMap[gname] { cmap[code] = mapped }
                                else if gname.count == 1 { cmap[code] = gname }
                            }
                        }
                    }
                }
            }
        }
    }
    return cmap
}

final class ScanCtx {
    var cs: CGPDFContentStreamRef
    var fontCache: [String: [Int: String]] = [:]
    var hasRichFont: Bool = false
    var curFontName: String = ""
    var curPt: CGFloat = 10.0
    var tm: CGAffineTransform = .identity
    var tlm: CGAffineTransform = .identity
    var tl: CGFloat = 0.0
    var ctm: CGAffineTransform = .identity
    var ctmStack: [CGAffineTransform] = []
    var pathStart: CGPoint?
    var pathHorizWidth: CGFloat = 0.0
    var baseY: CGFloat?
    var tokens: [String] = []

    init(cs: CGPDFContentStreamRef) { self.cs = cs }

    func decodePDFString(_ pdfStr: CGPDFStringRef) -> String {
        let len = CGPDFStringGetLength(pdfStr)
        guard len > 0, let ptr = CGPDFStringGetBytePtr(pdfStr) else { return "" }
        let cmap = fontCache[curFontName] ?? [:]
        var out = ""
        for i in 0..<len {
            let code = Int(ptr[i])
            if let mapped = cmap[code] {
                out += mapped
            } else if code >= 32 && code < 127, let sc = UnicodeScalar(code) {
                out.append(Character(sc))
            }
        }
        return out
    }

    func emitText(_ s: String) {
        guard !s.isEmpty else { return }
        let pagePt = CGPoint(x: tm.tx, y: tm.ty).applying(ctm)
        let effPt = curPt * hypot(tm.a, tm.b) * hypot(ctm.a, ctm.b)
        if effPt < 8.5, let by = baseY {
            let relY = pagePt.y - by
            let trimmed = s.trimmingCharacters(in: .whitespaces)
            if !trimmed.isEmpty && trimmed.count <= 12 && !trimmed.contains(" ") {
                if relY > 1.5 && relY <= 9.0 { tokens.append("^{\(trimmed)}"); return }
                if relY < -0.7 && relY >= -7.5 { tokens.append("_{\(trimmed)}"); return }
            }
        }
        if let by = baseY, abs(pagePt.y - by) > 9.5 {
            baseY = pagePt.y
        } else if effPt >= 8.8 || baseY == nil {
            baseY = pagePt.y
        }
        tokens.append(s)
    }
}

func scanPageWithCoreGraphics(_ cgPage: CGPDFPage) -> (String, Bool) {
    let cs = CGPDFContentStreamCreateWithPage(cgPage)
    defer { CGPDFContentStreamRelease(cs) }
    let ctx = ScanCtx(cs: cs)
    guard let opTable = CGPDFOperatorTableCreate() else { return ("", false) }
    defer { CGPDFOperatorTableRelease(opTable) }

    CGPDFOperatorTableSetCallback(opTable, "q") { _, info in
        guard let info = info else { return }
        Unmanaged<ScanCtx>.fromOpaque(info).takeUnretainedValue().ctmStack.append(
            Unmanaged<ScanCtx>.fromOpaque(info).takeUnretainedValue().ctm
        )
    }
    CGPDFOperatorTableSetCallback(opTable, "Q") { _, info in
        guard let info = info else { return }
        let c = Unmanaged<ScanCtx>.fromOpaque(info).takeUnretainedValue()
        if let last = c.ctmStack.popLast() { c.ctm = last }
        c.pathStart = nil; c.pathHorizWidth = 0
    }
    CGPDFOperatorTableSetCallback(opTable, "cm") { scanner, info in
        guard let info = info else { return }
        let c = Unmanaged<ScanCtx>.fromOpaque(info).takeUnretainedValue()
        var f: CGPDFReal = 0, e: CGPDFReal = 0, d: CGPDFReal = 0, cc: CGPDFReal = 0, b: CGPDFReal = 0, a: CGPDFReal = 0
        guard CGPDFScannerPopNumber(scanner, &f), CGPDFScannerPopNumber(scanner, &e),
              CGPDFScannerPopNumber(scanner, &d), CGPDFScannerPopNumber(scanner, &cc),
              CGPDFScannerPopNumber(scanner, &b), CGPDFScannerPopNumber(scanner, &a) else { return }
        c.ctm = CGAffineTransform(a: a, b: b, c: cc, d: d, tx: e, ty: f).concatenating(c.ctm)
    }
    CGPDFOperatorTableSetCallback(opTable, "m") { scanner, info in
        guard let info = info else { return }
        let c = Unmanaged<ScanCtx>.fromOpaque(info).takeUnretainedValue()
        var y: CGPDFReal = 0, x: CGPDFReal = 0
        guard CGPDFScannerPopNumber(scanner, &y), CGPDFScannerPopNumber(scanner, &x) else { return }
        c.pathStart = CGPoint(x: x, y: y).applying(c.ctm)
        c.pathHorizWidth = 0
    }
    CGPDFOperatorTableSetCallback(opTable, "l") { scanner, info in
        guard let info = info else { return }
        let c = Unmanaged<ScanCtx>.fromOpaque(info).takeUnretainedValue()
        var y: CGPDFReal = 0, x: CGPDFReal = 0
        guard CGPDFScannerPopNumber(scanner, &y), CGPDFScannerPopNumber(scanner, &x) else { return }
        let pt = CGPoint(x: x, y: y).applying(c.ctm)
        if let st = c.pathStart, abs(pt.y - st.y) < 1.5 { c.pathHorizWidth = abs(pt.x - st.x) }
    }
    CGPDFOperatorTableSetCallback(opTable, "re") { scanner, info in
        guard let info = info else { return }
        let c = Unmanaged<ScanCtx>.fromOpaque(info).takeUnretainedValue()
        var rh: CGPDFReal = 0, rw: CGPDFReal = 0, ry: CGPDFReal = 0, rx: CGPDFReal = 0
        guard CGPDFScannerPopNumber(scanner, &rh), CGPDFScannerPopNumber(scanner, &rw),
              CGPDFScannerPopNumber(scanner, &ry), CGPDFScannerPopNumber(scanner, &rx) else { return }
        let p0 = CGPoint(x: rx, y: ry).applying(c.ctm)
        let p1 = CGPoint(x: rx + rw, y: ry + rh).applying(c.ctm)
        if abs(p1.y - p0.y) < 1.8 { c.pathHorizWidth = abs(p1.x - p0.x) }
    }
    let strokeCb: CGPDFOperatorCallback = { _, info in
        guard let info = info else { return }
        let c = Unmanaged<ScanCtx>.fromOpaque(info).takeUnretainedValue()
        if c.pathHorizWidth >= 3.0 && c.pathHorizWidth <= 115.0 { c.tokens.append(" / [FRAC_BAR] / ") }
        c.pathStart = nil; c.pathHorizWidth = 0
    }
    for op in ["S", "s", "f", "F"] { CGPDFOperatorTableSetCallback(opTable, op, strokeCb) }

    CGPDFOperatorTableSetCallback(opTable, "BT") { _, info in
        guard let info = info else { return }
        let c = Unmanaged<ScanCtx>.fromOpaque(info).takeUnretainedValue()
        c.tm = .identity; c.tlm = .identity; c.baseY = nil
    }
    CGPDFOperatorTableSetCallback(opTable, "Tf") { scanner, info in
        guard let info = info else { return }
        let c = Unmanaged<ScanCtx>.fromOpaque(info).takeUnretainedValue()
        var sz: CGPDFReal = 10.0
        var fnamePtr: UnsafePointer<CChar>?
        guard CGPDFScannerPopNumber(scanner, &sz), CGPDFScannerPopName(scanner, &fnamePtr), let fp = fnamePtr else { return }
        let fname = String(cString: fp)
        c.curFontName = fname; c.curPt = sz
        if c.fontCache[fname] == nil {
            if let fObj = CGPDFContentStreamGetResource(c.cs, "Font", fp) {
                var fDict: CGPDFDictionaryRef?
                if CGPDFObjectGetValue(fObj, .dictionary, &fDict), let fd = fDict {
                    let parsed = parseFontMap(fd)
                    if !parsed.isEmpty { c.hasRichFont = true }
                    c.fontCache[fname] = parsed
                }
            }
        }
    }
    CGPDFOperatorTableSetCallback(opTable, "Td") { scanner, info in
        guard let info = info else { return }
        let c = Unmanaged<ScanCtx>.fromOpaque(info).takeUnretainedValue()
        var ty: CGPDFReal = 0, tx: CGPDFReal = 0
        guard CGPDFScannerPopNumber(scanner, &ty), CGPDFScannerPopNumber(scanner, &tx) else { return }
        let prevPageY = CGPoint(x: c.tm.tx, y: c.tm.ty).applying(c.ctm).y
        c.tlm = CGAffineTransform(translationX: tx, y: ty).concatenating(c.tlm)
        c.tm = c.tlm
        let newPageY = CGPoint(x: c.tm.tx, y: c.tm.ty).applying(c.ctm).y
        let effPt = c.curPt * hypot(c.tm.a, c.tm.b) * hypot(c.ctm.a, c.ctm.b)
        if effPt >= 8.8 {
            if c.baseY != nil && abs(newPageY - prevPageY) > 8.8 { c.tokens.append("\n") }
            c.baseY = newPageY
        }
    }
    CGPDFOperatorTableSetCallback(opTable, "TD") { scanner, info in
        guard let info = info else { return }
        let c = Unmanaged<ScanCtx>.fromOpaque(info).takeUnretainedValue()
        var ty: CGPDFReal = 0, tx: CGPDFReal = 0
        guard CGPDFScannerPopNumber(scanner, &ty), CGPDFScannerPopNumber(scanner, &tx) else { return }
        c.tl = -ty
        let prevPageY = CGPoint(x: c.tm.tx, y: c.tm.ty).applying(c.ctm).y
        c.tlm = CGAffineTransform(translationX: tx, y: ty).concatenating(c.tlm)
        c.tm = c.tlm
        let newPageY = CGPoint(x: c.tm.tx, y: c.tm.ty).applying(c.ctm).y
        let effPt = c.curPt * hypot(c.tm.a, c.tm.b) * hypot(c.ctm.a, c.ctm.b)
        if effPt >= 8.8 {
            if c.baseY != nil && abs(newPageY - prevPageY) > 8.8 { c.tokens.append("\n") }
            c.baseY = newPageY
        }
    }
    CGPDFOperatorTableSetCallback(opTable, "Tm") { scanner, info in
        guard let info = info else { return }
        let c = Unmanaged<ScanCtx>.fromOpaque(info).takeUnretainedValue()
        var f: CGPDFReal = 0, e: CGPDFReal = 0, d: CGPDFReal = 0, cc: CGPDFReal = 0, b: CGPDFReal = 0, a: CGPDFReal = 0
        guard CGPDFScannerPopNumber(scanner, &f), CGPDFScannerPopNumber(scanner, &e),
              CGPDFScannerPopNumber(scanner, &d), CGPDFScannerPopNumber(scanner, &cc),
              CGPDFScannerPopNumber(scanner, &b), CGPDFScannerPopNumber(scanner, &a) else { return }
        let prevPageY = CGPoint(x: c.tm.tx, y: c.tm.ty).applying(c.ctm).y
        c.tlm = CGAffineTransform(a: a, b: b, c: cc, d: d, tx: e, ty: f)
        c.tm = c.tlm
        let newPageY = CGPoint(x: c.tm.tx, y: c.tm.ty).applying(c.ctm).y
        let effPt = c.curPt * hypot(c.tm.a, c.tm.b) * hypot(c.ctm.a, c.ctm.b)
        if effPt >= 8.8 {
            if c.baseY != nil && abs(newPageY - prevPageY) > 8.8 { c.tokens.append("\n") }
            c.baseY = newPageY
        }
    }
    CGPDFOperatorTableSetCallback(opTable, "T*") { _, info in
        guard let info = info else { return }
        let c = Unmanaged<ScanCtx>.fromOpaque(info).takeUnretainedValue()
        c.tlm = CGAffineTransform(translationX: 0, y: -c.tl).concatenating(c.tlm)
        c.tm = c.tlm
        c.tokens.append("\n")
        c.baseY = CGPoint(x: c.tm.tx, y: c.tm.ty).applying(c.ctm).y
    }
    CGPDFOperatorTableSetCallback(opTable, "Tj") { scanner, info in
        guard let info = info else { return }
        let c = Unmanaged<ScanCtx>.fromOpaque(info).takeUnretainedValue()
        var pdfStr: CGPDFStringRef?
        if CGPDFScannerPopString(scanner, &pdfStr), let ps = pdfStr { c.emitText(c.decodePDFString(ps)) }
    }
    CGPDFOperatorTableSetCallback(opTable, "TJ") { scanner, info in
        guard let info = info else { return }
        let c = Unmanaged<ScanCtx>.fromOpaque(info).takeUnretainedValue()
        var arr: CGPDFArrayRef?
        guard CGPDFScannerPopArray(scanner, &arr), let a = arr else { return }
        var buf = ""
        let cnt = CGPDFArrayGetCount(a)
        for i in 0..<cnt {
            var ps: CGPDFStringRef?
            var num: CGPDFReal = 0
            if CGPDFArrayGetString(a, i, &ps), let s = ps { buf += c.decodePDFString(s) }
            else if CGPDFArrayGetNumber(a, i, &num), num <= -220 { buf += " " }
        }
        c.emitText(buf)
    }

    let unmanaged = Unmanaged.passUnretained(ctx)
    let scanner = CGPDFScannerCreate(cs, opTable, unmanaged.toOpaque())
    defer { CGPDFScannerRelease(scanner) }
    _ = CGPDFScannerScan(scanner)
    return (ctx.tokens.joined(), ctx.hasRichFont)
}

guard CommandLine.arguments.count >= 2 else {
    fputs("Usage: inspect_pdf <pdf_path>\n", stderr)
    exit(2)
}
let pdfURL = URL(fileURLWithPath: CommandLine.arguments[1])
guard let doc = PDFDocument(url: pdfURL), doc.pageCount > 0 else {
    fputs("Failed to open valid PDFDocument at \(pdfURL.path)\n", stderr)
    exit(1)
}
var pages: [[String: Any]] = []
for idx in 0..<doc.pageCount {
    guard let page = doc.page(at: idx) else {
        pages.append(["page": idx + 1, "text": ""])
        continue
    }
    let pdfKitTxt = page.string ?? ""
    var chosenTxt = pdfKitTxt
    if let cgPage = page.pageRef {
        let (streamTxt, hasRichFont) = scanPageWithCoreGraphics(cgPage)
        if hasRichFont {
            let sAlnum = streamTxt.unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) }.count
            let bAlnum = pdfKitTxt.unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) }.count
            if sAlnum >= max(Int(Double(bAlnum) * 0.75), 1) {
                chosenTxt = streamTxt
            }
        }
    }
    pages.append(["page": idx + 1, "text": chosenTxt])
}
let payload: [String: Any] = ["total_pages": doc.pageCount, "pages": pages]
let data = try JSONSerialization.data(withJSONObject: payload, options: [])
if let s = String(data: data, encoding: .utf8) {
    print(s)
}
"""


SWIFT_PDF_RENDERER: str = r"""
import Foundation
import PDFKit
import AppKit

let args = CommandLine.arguments
guard args.count >= 5 else {
    fputs("Usage: render_pdf <pdf_path> <out_dir> <dpi> <pages_csv> [prefix]\n", stderr)
    exit(2)
}

let pdfURL = URL(fileURLWithPath: args[1])
let outDir = URL(fileURLWithPath: args[2])
let dpi = Double(args[3]) ?? 200.0
let pagesCSV = args[4]
let filePrefix = args.count >= 6 ? args[5] : ""

guard let doc = PDFDocument(url: pdfURL), doc.pageCount > 0 else {
    fputs("Failed to open valid PDFDocument at \(pdfURL.path)\n", stderr)
    exit(1)
}

let totalPages = doc.pageCount
let requestedPages: [Int] = pagesCSV.split(separator: ",").compactMap { Int($0) }.filter { $0 >= 1 && $0 <= totalPages }
let scale = CGFloat(max(dpi, 72.0) / 72.0)

var written: [String] = []
for pageNum in requestedPages {
    guard let page = doc.page(at: pageNum - 1) else { continue }
    let mediaBounds = page.bounds(for: .mediaBox)
    let width = max(Int((mediaBounds.width * scale).rounded()), 1)
    let height = max(Int((mediaBounds.height * scale).rounded()), 1)

    guard let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: width,
        pixelsHigh: height,
        bitsPerSample: 8,
        samplesPerPixel: 4,
        hasAlpha: true,
        isPlanar: false,
        colorSpaceName: .deviceRGB,
        bytesPerRow: 0,
        bitsPerPixel: 0
    ) else {
        continue
    }

    NSGraphicsContext.saveGraphicsState()
    if let ctx = NSGraphicsContext(bitmapImageRep: rep) {
        NSGraphicsContext.current = ctx
        let cg = ctx.cgContext
        cg.setFillColor(NSColor.white.cgColor)
        cg.fill(CGRect(x: 0, y: 0, width: CGFloat(width), height: CGFloat(height)))
        cg.interpolationQuality = .high
        cg.scaleBy(x: scale, y: scale)
        page.draw(with: .mediaBox, to: cg)
    }
    NSGraphicsContext.restoreGraphicsState()

    guard let pngData = rep.representation(using: .png, properties: [:]) else { continue }
    let fileName = String(format: "%@page_%03d.png", filePrefix, pageNum)
    let outURL = outDir.appendingPathComponent(fileName)
    try pngData.write(to: outURL)
    written.append(outURL.path)
}

let payload: [String: Any] = ["total_pages": totalPages, "rendered": written]
let jsonData = try JSONSerialization.data(withJSONObject: payload, options: [])
if let s = String(data: jsonData, encoding: .utf8) {
    print(s)
}
"""


SWIFT_SVG_RENDERER: str = r"""
import Foundation
import AppKit

let args = CommandLine.arguments
guard args.count >= 4 else {
    fputs("Usage: render_svg <svg_path> <png_out_path> <target_width>\n", stderr)
    exit(2)
}

let svgURL = URL(fileURLWithPath: args[1])
let outURL = URL(fileURLWithPath: args[2])
let targetWidth = CGFloat(Int(args[3]) ?? 1100)

guard let image = NSImage(contentsOf: svgURL) else {
    fputs("Failed to load SVG image at \(svgURL.path)\n", stderr)
    exit(1)
}

let origSize = image.size
let baseW = origSize.width > 1.0 ? origSize.width : targetWidth
let baseH = origSize.height > 1.0 ? origSize.height : (targetWidth * 0.5)
let ratio = max(targetWidth / baseW, 1.0)
let width = max(Int((baseW * ratio).rounded()), 64)
let height = max(Int((baseH * ratio).rounded()), 32)
let pad = 24
let canvasW = width + 2 * pad
let canvasH = height + 2 * pad

guard let rep = NSBitmapImageRep(
    bitmapDataPlanes: nil,
    pixelsWide: canvasW,
    pixelsHigh: canvasH,
    bitsPerSample: 8,
    samplesPerPixel: 4,
    hasAlpha: true,
    isPlanar: false,
    colorSpaceName: .deviceRGB,
    bytesPerRow: 0,
    bitsPerPixel: 0
) else {
    exit(1)
}

NSGraphicsContext.saveGraphicsState()
if let ctx = NSGraphicsContext(bitmapImageRep: rep) {
    NSGraphicsContext.current = ctx
    ctx.imageInterpolation = .high
    NSColor.white.setFill()
    NSRect(x: 0, y: 0, width: canvasW, height: canvasH).fill()
    image.draw(
        in: NSRect(x: pad, y: pad, width: width, height: height),
        from: NSRect(origin: .zero, size: origSize),
        operation: .sourceOver,
        fraction: 1.0
    )
}
NSGraphicsContext.restoreGraphicsState()

guard let pngData = rep.representation(using: .png, properties: [:]) else {
    exit(1)
}
try pngData.write(to: outURL)
print(outURL.path)
"""


# ============================================================================
# Section 2: Domain Governance Bridge, Binary Raster Validation & SVG Transcoding
# ============================================================================

def _load_pipeline_guards(cwd: Path) -> Optional[object]:
    """Load ``pipeline_guards.py`` from the workspace or user Copilot directory.

    Args:
        cwd: Active workspace root directory.

    Returns:
        Imported ``pipeline_guards`` module object, or ``None`` if unavailable.
    """
    copilot_home = (
        Path(os.environ["COPILOT_HOME"]).expanduser()
        if os.environ.get("COPILOT_HOME", "").strip()
        else (Path.home() / ".copilot")
    )
    candidates = [
        Path(__file__).resolve().parents[3] / "hooks" / "scripts" / "pipeline_guards.py",
        cwd / ".github" / "hooks" / "scripts" / "pipeline_guards.py",
        copilot_home / "hooks" / "scripts" / "pipeline_guards.py",
    ]
    for candidate in candidates:
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location("facet_pipeline_guards", str(candidate))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
    return None


def _check_url_approved(url: str, cwd: Path, guards_mod: Optional[object]) -> Tuple[bool, str]:
    """Validate a URL against the 3-layer Approved Research Sources Registry.

    Args:
        url: Remote HTTP(S) URL to verify.
        cwd: Workspace root directory used to merge project-level sources.
        guards_mod: Loaded ``pipeline_guards`` module providing domain helpers.

    Returns:
        Tuple ``(approved, denial_reason)``.
    """
    # Reject non-HTTP(S) schemes (such as `file://`, `ftp://`, or `javascript:`) immediately.
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in ("https", "http"):
        return (False, f"Unsupported URL scheme '{parsed.scheme}' in '{url}'.")
    if guards_mod is None:
        return (False, "pipeline_guards.py could not be loaded to verify approved research domains.")
    # Merge built-in academic domains, user `~/.copilot/approved_sources.local.json`,
    # and workspace `.pipeline_config.json` domains before checking the hostname.
    approved_data = guards_mod.load_approved_research_sources(cwd)
    domains = approved_data.get("domains", [])
    if guards_mod.is_approved_research_url(url, domains):
        return (True, "")
    host = parsed.hostname or url
    return (
        False,
        f"Domain '{host}' is not in the Approved Research Sources Registry. "
        f"Ask the user to authorize it via '/add-source {host}' before extracting from '{url}'.",
    )


class UnapprovedRedirectError(PermissionError):
    """HTTP redirect attempted to exit the Approved Research Sources Registry."""


def parse_page_selection(pages_spec: Optional[str], max_pages: int) -> List[int]:
    """Parse a 1-based page specification bounded by ``max_pages``.

    Args:
        pages_spec: Comma-separated pages/ranges (for example ``"1-4,7"``), or
            ``None`` to select ``1..max_pages``.
        max_pages: Maximum number of pages to return.

    Returns:
        Deduplicated list of 1-based page numbers capped at ``max_pages``.

    Raises:
        ValueError: If ``max_pages < 1`` or ``pages_spec`` contains invalid syntax.
    """
    if max_pages < 1:
        raise ValueError("max_pages must be >= 1.")
    if not pages_spec or not pages_spec.strip():
        return list(range(1, max_pages + 1))
    selected: List[int] = []
    for part in pages_spec.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            bounds = token.split("-", 1)
            if not bounds[0].strip().isdigit() or not bounds[1].strip().isdigit():
                raise ValueError(f"Invalid page range specifier '{token}'. Expected positive 1-based integers like '1-4'.")
            start = int(bounds[0].strip())
            end = int(bounds[1].strip())
            if start < 1 or end < start:
                raise ValueError(f"Invalid page range '{token}' (must satisfy 1 <= start <= end).")
            for p in range(start, end + 1):
                if p not in selected:
                    selected.append(p)
        else:
            if not token.isdigit() or int(token) < 1:
                raise ValueError(f"Invalid page number '{token}'. Page numbers are 1-based positive integers.")
            p = int(token)
            if p not in selected:
                selected.append(p)
    if not selected:
        raise ValueError(f"Page selection '{pages_spec}' yielded zero pages.")
    return selected[:max_pages]


def validate_and_inspect_raster(data: bytes) -> Tuple[Optional[str], Optional[Tuple[int, int]], Optional[str]]:
    r"""Validate raster framing, chunk integrity, and pixel dimensions.

    Supports PNG (chunk walk, IHDR, CRC-32, zlib IDAT check, IEND), JPEG
    (SOI/SOF/SOS/EOI), GIF, WEBP, BMP, and TIFF streams.

    Args:
        data: Raw image bytes to inspect.

    Returns:
        Tuple ``(canonical_format, dimensions, error_reason)`` where
        ``dimensions`` is ``(width, height)`` when available and ``error_reason``
        is ``None`` on valid rasters.
    """
    if not data or len(data) < 10:
        return (None, None, "Empty or truncated byte stream (< 10 bytes).")

    # 1. PNG: full chunk walk, IHDR check, CRC-32 verification, IDAT zlib decompression, and IEND check
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        pos = 8
        n = len(data)
        width = 0
        height = 0
        saw_ihdr = False
        saw_iend = False
        idat_parts: List[bytes] = []
        # Repeat step-by-step while `pos + 8 <= n`.
        while pos + 8 <= n:
            c_len = struct.unpack(">I", data[pos : pos + 4])[0]
            c_type = data[pos + 4 : pos + 8]
            data_end = pos + 8 + c_len
            crc_end = data_end + 4
            if crc_end > n:
                return (None, None, f"Truncated PNG chunk '{c_type.decode('latin1', 'replace')}'.")
            c_data = data[pos + 8 : data_end]
            expected_crc = struct.unpack(">I", data[data_end:crc_end])[0]
            actual_crc = zlib.crc32(c_type + c_data) & 0xFFFFFFFF
            if c_type in (b"IHDR", b"IDAT", b"IEND") and actual_crc != expected_crc:
                return (None, None, f"Corrupted PNG CRC in '{c_type.decode('ascii')}' chunk.")
            if c_type == b"IHDR":
                if c_len != 13:
                    return (None, None, "Malformed PNG IHDR length.")
                width, height = struct.unpack(">II", c_data[:8])
                if width <= 0 or height <= 0:
                    return (None, None, "Invalid zero/negative PNG dimensions.")
                saw_ihdr = True
            elif c_type == b"IDAT":
                idat_parts.append(c_data)
            elif c_type == b"IEND":
                saw_iend = True
                break
            pos = crc_end
        if not saw_ihdr or not saw_iend or not idat_parts:
            return (None, None, "Incomplete PNG stream (missing IHDR, IDAT, or IEND).")
        # Decompress deflated binary stream, ignoring non-zlib or truncated segments.
        try:
            decompressed = zlib.decompress(b"".join(idat_parts))
            if len(decompressed) < height:
                return (None, None, "Decompressed PNG IDAT payload smaller than scanline count.")
        except Exception as exc:
            return (None, None, f"Corrupted PNG IDAT deflate stream: {exc}")
        return ("png", (int(width), int(height)), None)

    # 2. JPEG: SOI + SOF frame dimensions + SOS entropy payload + EOI check
    if data[:2] == b"\xff\xd8":
        if b"\xff\xd9" not in data[-64:] and b"\xff\xd9" not in data[4:]:
            return (None, None, "Truncated JPEG stream (missing EOI 0xFFD9 marker).")
        idx = 2
        n = len(data)
        sof_markers = {
            0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
        }
        dims: Optional[Tuple[int, int]] = None
        saw_sos = False
        # Repeat step-by-step while `idx + 2 <= n`.
        while idx + 2 <= n:
            if data[idx] != 0xFF:
                idx += 1
                continue
            # Repeat step-by-step while `idx < n and data[idx] == 255`.
            while idx < n and data[idx] == 0xFF:
                idx += 1
            if idx >= n:
                break
            marker = data[idx]
            idx += 1
            # Stop scanning further once `marker == 217`.
            if marker == 0xD9:
                break
            if marker == 0xD8 or (0xD0 <= marker <= 0xD7):
                continue
            if idx + 2 > n:
                return (None, None, "Truncated JPEG segment length.")
            seg_len = struct.unpack(">H", data[idx : idx + 2])[0]
            if seg_len < 2 or idx + seg_len > n:
                return (None, None, "Truncated JPEG segment body.")
            if marker in sof_markers:
                if seg_len < 7:
                    return (None, None, "Malformed JPEG SOF segment.")
                h_val, w_val = struct.unpack(">HH", data[idx + 3 : idx + 7])
                if w_val <= 0 or h_val <= 0:
                    return (None, None, "Invalid zero JPEG dimensions.")
                dims = (int(w_val), int(h_val))
            elif marker == 0xDA:  # SOS: entropy-coded scan data follows until EOI
                saw_sos = (idx + seg_len + 2) <= n
                break
            idx += seg_len
        if dims is None or not saw_sos:
            return (None, None, "Incomplete JPEG stream (missing SOF frame or SOS scan data).")
        return ("jpg", dims, None)

    # 3. GIF: GIF87a / GIF89a + trailer 0x3B
    if data[:6] in (b"GIF87a", b"GIF89a"):
        width, height = struct.unpack("<HH", data[6:10])
        if width <= 0 or height <= 0 or data.rstrip()[-1:] != b";":
            return (None, None, "Truncated or invalid GIF stream.")
        return ("gif", (int(width), int(height)), None)

    # 4. WEBP: RIFF....WEBP + VP8 / VP8L / VP8X
    if len(data) >= 30 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        riff_size = struct.unpack("<I", data[4:8])[0]
        if riff_size + 8 > len(data):
            return (None, None, "Truncated RIFF WEBP container.")
        chunk_tag = data[12:16]
        if chunk_tag == b"VP8X" and len(data) >= 30:
            w_m1 = int.from_bytes(data[24:27], "little")
            h_m1 = int.from_bytes(data[27:30], "little")
            return ("webp", (w_m1 + 1, h_m1 + 1), None)
        if chunk_tag == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
            b0, b1, b2, b3 = data[21], data[22], data[23], data[24]
            w_val = 1 + (((b1 & 0x3F) << 8) | b0)
            h_val = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
            return ("webp", (int(w_val), int(h_val)), None)
        if chunk_tag == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
            w_val = struct.unpack("<H", data[26:28])[0] & 0x3FFF
            h_val = struct.unpack("<H", data[28:30])[0] & 0x3FFF
            return ("webp", (int(w_val), int(h_val)), None)
        return ("webp", None, None)

    # 5. BMP: BM header + DIB width/height
    if len(data) >= 26 and data[:2] == b"BM":
        declared_size = struct.unpack("<I", data[2:6])[0]
        if declared_size > len(data):
            return (None, None, "Truncated BMP file.")
        dib_size = struct.unpack("<I", data[14:18])[0]
        if dib_size >= 40 and len(data) >= 26:
            w_val, h_raw = struct.unpack("<ii", data[18:26])
            h_val = abs(h_raw)
            if w_val > 0 and h_val > 0:
                return ("bmp", (int(w_val), int(h_val)), None)
        return (None, None, "Malformed BMP DIB header.")

    # 6. TIFF: little-endian II*\x00 or big-endian MM\x00*
    if len(data) >= 16 and data[:4] in (b"II*\x00", b"MM\x00*"):
        endian = "<" if data[:2] == b"II" else ">"
        ifd_off = struct.unpack(endian + "I", data[4:8])[0]
        if ifd_off + 2 > len(data):
            return (None, None, "Truncated TIFF IFD offset.")
        num_entries = struct.unpack(endian + "H", data[ifd_off : ifd_off + 2])[0]
        if ifd_off + 2 + num_entries * 12 > len(data):
            return (None, None, "Truncated TIFF IFD entries.")
        w_opt: Optional[int] = None
        h_opt: Optional[int] = None
        for idx in range(num_entries):
            ent = data[ifd_off + 2 + idx * 12 : ifd_off + 2 + (idx + 1) * 12]
            tag, field_type, count = struct.unpack(endian + "HHI", ent[:8])
            val_bytes = ent[8:12]
            if tag in (256, 257) and count == 1:
                val = struct.unpack(endian + "H", val_bytes[:2])[0] if field_type == 3 else struct.unpack(endian + "I", val_bytes)[0]
                if tag == 256:
                    w_opt = int(val)
                else:
                    h_opt = int(val)
        if w_opt and h_opt and w_opt > 0 and h_opt > 0:
            return ("tiff", (w_opt, h_opt), None)
        return ("tiff", None, None)

    return (None, None, "Unrecognized or corrupted image header.")


def transcode_legacy_raster_to_png(raw_bytes: bytes, src_suffix: str, out_png: Path) -> bool:
    """Convert BMP, TIFF, or GIF bytes into a verified PNG via macOS ``sips``.

    Args:
        raw_bytes: Validated legacy raster bytes.
        src_suffix: Native file suffix (e.g. ``".bmp"``, ``".tiff"``, ``".gif"``).
        out_png: Destination PNG file path.

    Returns:
        ``True`` if ``sips`` produced a valid PNG file at ``out_png``, else ``False``.
    """
    with tempfile.NamedTemporaryFile(suffix=src_suffix, delete=False) as tmp:
        tmp.write(raw_bytes)
        tmp_src = Path(tmp.name)
    try:
        sips_bin = shutil.which("sips")
        if sips_bin and sys.platform == "darwin":
            # Rasterize or resize the visual asset into a PNG plate using macOS `sips`.
            res = subprocess.run(
                [sips_bin, "-s", "format", "png", str(tmp_src), "--out", str(out_png)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            # Return `True` once the renderer succeeds and writes a non-empty PNG output file.
            if res.returncode == 0 and out_png.is_file():
                fmt, _dims, err = validate_and_inspect_raster(out_png.read_bytes())
                # Return early when `fmt == 'png' and err is None`.
                if fmt == "png" and err is None:
                    return True
                out_png.unlink(missing_ok=True)
        return False
    finally:
        tmp_src.unlink(missing_ok=True)


def is_decorative_icon(
    dims: Optional[Tuple[int, int]],
    declared_w: Optional[int] = None,
    declared_h: Optional[int] = None,
) -> bool:
    """Determine whether an image is a small decorative icon or tracking pixel.

    Preserves wide horizontal mathematical equation strips (for example,
    ``320x36`` PNG formulas) because their maximum dimension exceeds
    ``MIN_ICON_MAX_DIM`` and their area exceeds ``MIN_ICON_AREA``.

    Args:
        dims: Decoded ``(width, height)`` pixel dimensions when available.
        declared_w: Fallback HTML ``width`` attribute in pixels.
        declared_h: Fallback HTML ``height`` attribute in pixels.

    Returns:
        ``True`` if the image should be skipped as decorative, else ``False``.
    """
    w = dims[0] if dims else declared_w
    h = dims[1] if dims else declared_h
    if w is None or h is None:
        return False
    if w <= 0 or h <= 0:
        return True
    if w * h < MIN_ICON_AREA:
        return True
    if max(w, h) < MIN_ICON_MAX_DIM:
        return True
    return False


def render_svg_to_png(svg_path: Path, out_png: Path, target_width: int = DEFAULT_SVG_WIDTH) -> bool:
    """Rasterize an SVG diagram or equation to a white-padded PNG.

    Tries macOS Swift/AppKit first, then ``rsvg-convert``, then ``sips``.

    Args:
        svg_path: Existing input SVG file path.
        out_png: Destination PNG file path.
        target_width: Minimum target width in pixels.

    Returns:
        ``True`` when a non-empty PNG was written to ``out_png``, else ``False``.
    """
    assert svg_path.is_file(), f"SVG file does not exist: {svg_path}"
    swift_bin = shutil.which("swift")
    if swift_bin and sys.platform == "darwin":
        # Render the SVG diagram to a high-resolution PNG plate via macOS WebKit/AppKit.
        res = subprocess.run(
            [swift_bin, "-e", SWIFT_SVG_RENDERER, str(svg_path), str(out_png), str(target_width)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        # Return `True` once the renderer succeeds and writes a non-empty PNG output file.
        if res.returncode == 0 and out_png.is_file() and out_png.stat().st_size > 0:
            return True

    rsvg_bin = shutil.which("rsvg-convert")
    if rsvg_bin:
        # Rasterize the SVG diagram to PNG using `rsvg-convert`.
        res = subprocess.run(
            [rsvg_bin, "-w", str(target_width), "--background-color=white", "-o", str(out_png), str(svg_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Return `True` once the renderer succeeds and writes a non-empty PNG output file.
        if res.returncode == 0 and out_png.is_file() and out_png.stat().st_size > 0:
            return True

    sips_bin = shutil.which("sips")
    if sips_bin and sys.platform == "darwin":
        # Rasterize or resize the visual asset into a PNG plate using macOS `sips`.
        res = subprocess.run(
            [sips_bin, "-s", "format", "png", "-Z", str(target_width), str(svg_path), "--out", str(out_png)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Return `True` once the renderer succeeds and writes a non-empty PNG output file.
        if res.returncode == 0 and out_png.is_file() and out_png.stat().st_size > 0:
            return True

    return False


# ============================================================================
# Section 3: PDF Stream Inspection, `SOURCE_TEXT.md` Normalization & Lossless Plate Rendering
# ============================================================================

def _parse_max_pages_arg(raw: str) -> int:
    """Parse ``--max-pages`` integer or ``'all'`` (``0`` = uncapped all pages)."""
    val = str(raw).strip().lower()
    if val in ("all", "unlimited", "none", "0"):
        return 0
    parsed = int(val)
    if parsed < 0:
        raise argparse.ArgumentTypeError("--max-pages must be >= 0 or 'all'.")
    return parsed


def _postprocess_scanned_page_text(raw_text: str) -> str:
    r"""Coalesce adjacent subscript/superscript tokens emitted by ``CGPDFScanner``."""
    out = raw_text
    for _ in range(4):
        out = re.sub(r"_\{([^{}]*)\}_\{([^{}]*)\}", r"_{\1\2}", out)
        out = re.sub(r"\^\{([^{}]*)\}\^\{([^{}]*)\}", r"^{\1\2}", out)
    return out.replace("↦→", "↦")


def inspect_pdf_document(pdf_path: Path) -> Tuple[int, List[str]]:
    r"""Extract total page count and ``CGPDFScanner``/``PDFKit`` per-page text across ``1..N``."""
    swift_bin = shutil.which("swift")
    if swift_bin and sys.platform == "darwin":
        res = subprocess.run(
            [swift_bin, "-e", SWIFT_PDF_INSPECTOR, str(pdf_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if res.returncode == 0 and res.stdout.strip():
            payload = json.loads(res.stdout.strip().splitlines()[-1])
            total_pages = int(payload.get("total_pages") or 0)
            pages_raw = payload.get("pages") or []
            texts = [_postprocess_scanned_page_text(str(item.get("text") or "")) for item in pages_raw if isinstance(item, dict)]
            if total_pages > 0:
                while len(texts) < total_pages:
                    texts.append("")
                return (total_pages, texts[:total_pages])
        elif res.returncode != 0 and "Failed to open valid PDFDocument" in (res.stderr or ""):
            raise ValueError(f"Corrupted or unreadable PDF document '{pdf_path.name}'.")

    pdftotext_bin = shutil.which("pdftotext")
    pdfinfo_bin = shutil.which("pdfinfo")
    if pdftotext_bin and pdfinfo_bin:
        info_r = subprocess.run([pdfinfo_bin, str(pdf_path)], capture_output=True, text=True, timeout=30)
        m_pg = re.search(r"^Pages:\s+(\d+)", info_r.stdout or "", re.MULTILINE)
        if m_pg:
            total_p = int(m_pg.group(1))
            texts_p: List[str] = []
            for p_idx in range(1, total_p + 1):
                tr = subprocess.run(
                    [pdftotext_bin, "-f", str(p_idx), "-l", str(p_idx), str(pdf_path), "-"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                texts_p.append(tr.stdout if tr.returncode == 0 else "")
            return (total_p, texts_p)

    raw_b = pdf_path.read_bytes()
    approx_pages = max(len(re.findall(rb"/Type\s*/Page\b", raw_b)), 1)
    return (approx_pages, ["" for _ in range(approx_pages)])


def _score_pdf_page_visual_salience(page_num: int, page_text: str) -> Tuple[int, int, int, int]:
    r"""Score a PDF page across ``(scanned_or_garbled, figure_or_table, math_density, -page_num)``."""
    stripped = page_text.strip()
    garbled_chars = sum(1 for ch in stripped if ch == "\ufffd" or (ord(ch) < 32 and ch not in "\n\r\t"))
    is_scanned_or_garbled = 1 if (len(stripped) < 80 or (stripped and garbled_chars / len(stripped) > 0.02)) else 0
    has_fig_or_tbl = 1 if re.search(r"\b(?:figure|fig\.|table|algorithm|diagram|scheme|plate)\b", stripped, re.IGNORECASE) else 0
    eq_nums = len(re.findall(r"\(\d{1,3}\)", stripped))
    math_ops = sum(stripped.count(sym) for sym in ("∑", "∏", "∫", "∂", "∈", "≤", "≥", "±", "×", "∇", "√", "∞", "↦", "="))
    formal_hdrs = len(re.findall(r"\b(?:theorem|lemma|corollary|proposition|definition)\b", stripped, re.IGNORECASE))
    math_score = eq_nums * 3 + math_ops + formal_hdrs * 4
    return (is_scanned_or_garbled, has_fig_or_tbl, math_score, -page_num)


def select_pdf_pages_for_visual_plates(
    total_pages: int,
    page_texts: Sequence[str],
    pages_spec: Optional[str],
    max_pages: int,
    source_label: str,
) -> Tuple[List[int], Optional[str]]:
    r"""Auto-scale visual plates across ``1..N`` up to ``max_pages``, warning and salience-ranking above cap."""
    if pages_spec is not None and pages_spec.strip():
        req_explicit = parse_page_selection(pages_spec, max(total_pages, 1))
        in_range = [p for p in req_explicit if 1 <= p <= total_pages]
        if not in_range and total_pages > 0:
            raise ValueError(
                f"Requested pages {req_explicit} exceed total page count ({total_pages}) of '{source_label}'."
            )
        return (in_range, None)

    if max_pages <= 0 or total_pages <= max_pages:
        return (list(range(1, total_pages + 1)), None)

    scored = [
        (_score_pdf_page_visual_salience(idx + 1, page_texts[idx] if idx < len(page_texts) else ""), idx + 1)
        for idx in range(total_pages)
    ]
    scored.sort(reverse=True)
    chosen = sorted(p_num for _sc, p_num in scored[:max_pages])
    warning_msg = (
        f"Detected {total_pages}-page PDF '{source_label}' exceeding the {max_pages}-page visual plate warning cap; "
        f"staged {len(chosen)} salience-prioritized visual plates across pages 1..{total_pages} "
        f"(all {total_pages} embedded text pages preserved verbatim in SOURCE_TEXT.md). "
        f"Pass --max-pages {total_pages} (or --max-pages all) or set max_visual_pages in .pipeline_config.json to override."
    )
    return (chosen, warning_msg)


_PDF_LIGATURES_AND_DIACRITICS: Tuple[Tuple[str, str], ...] = (
    ("\ufb00", "ff"),
    ("\ufb01", "fi"),
    ("\ufb02", "fl"),
    ("\ufb03", "ffi"),
    ("\ufb04", "ffl"),
    ("´a", "á"),
    ("´c", "ć"),
    ("´e", "é"),
    ("´n", "ń"),
    ("´o", "ó"),
    ("´s", "ś"),
    ("´z", "ź"),
    ("¨a", "ä"),
    ("¨o", "ö"),
    ("¨u", "ü"),
    ("¨A", "Ä"),
    ("¨O", "Ö"),
    ("¨U", "Ü"),
)

_COMPOUND_HYPHEN_PREFIXES = {
    "multi", "high", "low", "real", "non", "sub", "super", "co", "pre", "post",
    "self", "cross", "two", "three", "one", "first", "second", "third", "higher",
    "lower", "mixed", "single", "double", "well", "ill", "time", "step", "zero",
    "closed", "open", "clean", "semi", "quasi", "anti", "inter", "intra", "over",
    "under", "fixed", "floating", "finite", "infinite", "matrix", "vector", "tensor",
}

_MATH_SHORT_WORDS = {
    "for", "all", "and", "or", "if", "in", "on", "to", "of", "by", "as",
    "min", "max", "sup", "inf", "lim", "exp", "log", "sin", "cos", "tan",
    "det", "dim", "ker", "arg", "mod", "gcd", "lcm",
}

_MATH_OPERATOR_CHARS = set("=∈∉⊂⊆∪∩≤≥≠≈≡±×÷·∂∇∑∏∫√∞↦→←↔∀∃⊤⊥∗")


_COMPOUND_HYPHEN_SUFFIXES = {
    "mode", "based", "valued", "order", "dimensional", "point", "scale", "free",
    "independent", "dependent", "like", "wise", "type", "rank", "step", "time",
    "space", "state", "qubit", "level", "aware", "safe", "driven", "guided",
    "invariant", "equivariant", "linear", "nonlinear", "specific", "agnostic",
}


def _clean_pdf_text_ligatures(text: str) -> str:
    r"""Normalize PDF ligature codepoints and decomposed TeX spacing diacritics."""
    out = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    for src_seq, dst_seq in _PDF_LIGATURES_AND_DIACRITICS:
        out = out.replace(src_seq, dst_seq)
    return out


def _join_hyphenated_prose_lines(prev_text: str, next_line: str) -> str:
    r"""Join two wrapped prose lines, de-hyphenating broken words while keeping compound hyphens."""
    prev_r = prev_text.rstrip()
    nxt_l = next_line.lstrip()
    if not prev_r:
        return nxt_l
    m_hyp = re.search(r"([A-Za-z0-9α-ωΑ-Ω]+)-$", prev_r)
    if m_hyp and nxt_l and nxt_l[0].islower():
        stem = m_hyp.group(1)
        nxt_word_m = re.match(r"^([a-z]+)", nxt_l)
        nxt_word = nxt_word_m.group(1) if nxt_word_m else ""
        keep_hyphen = (
            stem.lower() in _COMPOUND_HYPHEN_PREFIXES
            or len(stem) == 1
            or (stem[0].isupper() and nxt_word in _COMPOUND_HYPHEN_SUFFIXES)
        )
        if keep_hyphen:
            return prev_r + nxt_l
        return prev_r[:-1] + nxt_l
    if prev_r.endswith("–") and nxt_l:
        return prev_r + nxt_l
    return prev_r + " " + nxt_l


def _is_2d_math_fragment_line(line: str) -> bool:
    r"""Return True when ``line`` looks like a baseline-shattered 2D PDF equation fragment."""
    s = line.strip()
    if not s:
        return False
    if re.match(r"^(?:Abstract|Key\s*words|AMS\s+subject|Table\s+\d|Figure\s+\d|Fig\.\s*\d|REFERENCES|Bibliography)\b", s, re.IGNORECASE):
        return False
    if re.match(r"^(?:\d+(?:\.\d+)*\.?\s+[A-Z]|Theorem\b|Lemma\b|Corollary\b|Proposition\b|Definition\b|Remark\b|Proof\b)", s):
        return False
    if re.match(r"^\[\d+\]\s+[A-Z]", s):
        return False
    # Prose sentence openings or clauses ending with "such that" / "satisfying (k) is" belong in prose
    if re.match(r"^(?:For|Let|Here|Where|Since|Assume|When|The|This|We|In|If|Note)\s+[a-zA-Z]", s):
        return False
    if re.search(r"\b(?:such\s+that|satisfying\s+\(\d+\)\s+is|i\.e\.,)\b", s):
        return False
    # Tabular benchmark rows (2+ decimal measurements) are tables, not shattered 2D math
    if len(re.findall(r"\b\d+\.\d+\b", s)) >= 2:
        return False

    alpha_words = [
        w for w in re.findall(r"[A-Za-z]{3,}", s)
        if w.lower() not in _MATH_SHORT_WORDS
    ]
    has_math_op = any(ch in _MATH_OPERATOR_CHARS for ch in s) or ":=" in s or "̸=" in s
    has_eq_tag = bool(re.search(r"(?:^|[.,;\s])\(\d{1,3}\)\s*$", s))

    # Short isolated baseline tokens: numerators, limits, subscripts, differential/function shards, eq numbers
    if len(s) <= 22 and (len(alpha_words) == 0 or (" " not in s and len(s) <= 8)):
        return True
    if len(s) <= 24 and len(alpha_words) <= 1 and any(ch in s for ch in "()+-^_"):
        return True
    if has_eq_tag and len(alpha_words) <= 3:
        return True
    if has_math_op and len(alpha_words) <= 2 and not s.endswith((".", ":")):
        return True
    if has_math_op and len(alpha_words) <= 1:
        return True
    return False


def normalize_pdf_page_markdown(raw_page_text: str, page_num: int, total_pages: int) -> str:
    r"""Reflow PDF prose/tables/references and quarantine shattered 2D display-math glyph clusters."""
    cleaned = _clean_pdf_text_ligatures(raw_page_text)
    raw_lines = [ln.strip() for ln in cleaned.split("\n")]
    while raw_lines and not raw_lines[0]:
        raw_lines.pop(0)
    while raw_lines and not raw_lines[-1]:
        raw_lines.pop()
    if not raw_lines:
        return ""
    # Strip isolated running page number at top or bottom of page
    if len(raw_lines) > 1 and raw_lines[0] == str(page_num):
        raw_lines.pop(0)
    if len(raw_lines) > 1 and raw_lines[-1] == str(page_num):
        raw_lines.pop()

    # Pre-merge single-token orphaned super/subscript wrap at start of prose continuation (e.g. "∈Nd\n0, we define")
    merged_lines: List[str] = []
    idx = 0
    while idx < len(raw_lines):
        cur = raw_lines[idx]
        if (
            idx + 1 < len(raw_lines)
            and cur
            and not _is_2d_math_fragment_line(cur)
            and re.match(r"^[0-9],\s+[a-z]{2,}\b", raw_lines[idx + 1])
        ):
            merged_lines.append(f"{cur}_{raw_lines[idx + 1]}")
            idx += 2
            continue
        merged_lines.append(cur)
        idx += 1

    out_blocks: List[str] = []
    prose_buf: str = ""
    in_references = False
    in_table = False
    table_lines: List[str] = []
    ref_items: List[str] = []

    def _flush_prose() -> None:
        nonlocal prose_buf
        if prose_buf.strip():
            out_blocks.append(prose_buf.strip())
        prose_buf = ""

    def _flush_table() -> None:
        nonlocal in_table, table_lines
        if table_lines:
            out_blocks.append("```text\n" + "\n".join(table_lines) + "\n```")
        table_lines = []
        in_table = False

    def _flush_refs() -> None:
        nonlocal ref_items
        if ref_items:
            out_blocks.append("\n".join(f"- {it}" for it in ref_items))
        ref_items = []

    n_lines = len(merged_lines)
    i = 0
    while i < n_lines:
        ln = merged_lines[i]
        if not ln:
            _flush_prose()
            if in_table:
                _flush_table()
            i += 1
            continue

        if re.match(r"^(?:REFERENCES|Bibliography)\s*$", ln, re.IGNORECASE):
            _flush_prose()
            if in_table:
                _flush_table()
            in_references = True
            out_blocks.append(f"### {ln.title()}")
            i += 1
            continue

        if in_references:
            if re.match(r"^\[\d+\]", ln):
                ref_items.append(ln)
            elif ref_items:
                ref_items[-1] = _join_hyphenated_prose_lines(ref_items[-1], ln)
            else:
                ref_items.append(ln)
            i += 1
            continue

        # Detect Table caption + tabular region
        if re.match(r"^Table\s+\d+(?:\.\d+)*\s*$", ln):
            _flush_prose()
            in_table = True
            table_lines = [ln]
            i += 1
            continue

        if in_table:
            if re.match(r"^\d+(?:\.\d+)*\.\s+[A-Z]", ln):
                _flush_table()
            else:
                if (
                    table_lines
                    and re.search(r"∂\d+$", table_lines[-1])
                    and re.match(r"^\d+\b", ln)
                ):
                    table_lines[-1] = f"{table_lines[-1]}_{ln}"
                else:
                    table_lines.append(ln)
                i += 1
                continue

        # Check for multi-line shattered 2D display-math cluster (>= 2 consecutive math fragment lines)
        if _is_2d_math_fragment_line(ln):
            j = i + 1
            while j < n_lines and merged_lines[j] and _is_2d_math_fragment_line(merged_lines[j]):
                j += 1
            cluster = merged_lines[i:j]
            has_math_signal = any(
                any(ch in _MATH_OPERATOR_CHARS for ch in c) or ":=" in c or re.search(r"\(\d{1,3}\)\s*$", c)
                for c in cluster
            )
            if len(cluster) >= 2 and has_math_signal:
                _flush_prose()
                eq_nums = re.findall(r"\((\d{1,3})\)\s*$", "\n".join(cluster), re.MULTILINE)
                eq_lbl = f" (Eq. {', '.join('(' + e + ')' for e in eq_nums)}) " if eq_nums else " "
                q_lines = [
                    f"> **[2D Display Equation{eq_lbl}— PDF Stream Glyph, Script & Fraction-Bar Trace (`[FRAC_BAR]`); Cross-Check Exact Symbols/Subscripts Here Together With `SOURCE_OCR.md` Page {page_num} Visual Layout]**",
                    "> <details><summary>PDF stream glyph, subscript/superscript & fraction-bar tokens</summary>",
                    ">",
                    "> ```text",
                ]
                for c_ln in cluster:
                    q_lines.append(f"> {c_ln}")
                q_lines.append("> ```")
                q_lines.append("> </details>")
                out_blocks.append("\n".join(q_lines))
                i = j
                continue

        # Prose structural boundaries (Abstract, Key words, AMS, numbered sections, Corollary/Theorem, footnotes)
        if re.match(
            r"^(?:arXiv:|Abstract\.|Key\s*words\.|AMS\s+subject\s+classifications\.|\d+(?:\.\d+)*\.\s+[A-Z]|(?:Theorem|Lemma|Corollary|Proposition|Definition|Remark)\s+\d+|†)",
            ln,
        ):
            _flush_prose()
            prose_buf = ln
        else:
            prose_buf = _join_hyphenated_prose_lines(prose_buf, ln)
        i += 1

    _flush_prose()
    if in_table:
        _flush_table()
    if ref_items:
        _flush_refs()
    return "\n\n".join(out_blocks)


def persist_verbatim_source_text(
    cwd: Path,
    source_label: str,
    page_texts: Sequence[str],
    *,
    append: bool = False,
) -> Optional[Path]:
    r"""Write normalized ``1..N`` embedded PDF text to ``<cwd>/SOURCE_TEXT.md`` when text exists."""
    if not any(t.strip() for t in page_texts):
        return None
    total_p = len(page_texts)
    sec_lines: List[str] = [
        f"# Embedded PDF Text Layer: {source_label}",
        f"- **Total Pages Extracted:** {total_p}",
        "- **Channel Role:** Deterministic `1..N` PDF prose, citations, and tables (`SOURCE_TEXT.md`, untouched by `@Scribe`). Shattered 2D display-math glyph clusters are quarantined inside `<details>` blocks because untagged PDFs omit vector fraction bars and `CMEX` operator CMaps—use `SOURCE_OCR.md` for authoritative `$$ ... $$` KaTeX equations, figures, and scans.",
        "",
    ]
    for p_num, p_txt in enumerate(page_texts, 1):
        sec_lines.append(f"## Page {p_num} of {total_p}")
        cleaned_p = normalize_pdf_page_markdown(p_txt, p_num, total_p)
        if cleaned_p:
            sec_lines.append(cleaned_p)
        else:
            sec_lines.append(f"[No embedded text layer on Page {p_num} — see SOURCE_OCR.md visual transcription]")
        sec_lines.append("")
    new_block = "\n".join(sec_lines).rstrip() + "\n"

    target_md = cwd / "SOURCE_TEXT.md"
    final_text = new_block
    if append and target_md.is_file():
        existing = target_md.read_text(encoding="utf-8", errors="replace").rstrip()
        if existing:
            final_text = existing + "\n\n---\n\n" + new_block
    tmp_md = cwd / f".SOURCE_TEXT.{os.getpid()}.tmp"
    tmp_md.write_text(final_text, encoding="utf-8")
    os.replace(tmp_md, target_md)
    return target_md


def render_pdf_to_pngs(
    pdf_path: Path,
    out_dir: Path,
    dpi: int = DEFAULT_DPI,
    pages_spec: Optional[str] = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    file_prefix: str = "",
    selected_pages: Optional[Sequence[int]] = None,
) -> Tuple[List[Path], int, str]:
    """Render selected PDF pages to PNG plates inside ``out_dir``.

    Uses native macOS ``PDFKit`` (via Swift), falling back to system ``pdftoppm``.

    Args:
        pdf_path: Source PDF document path.
        out_dir: Staging output directory for rendered PNG files.
        dpi: Target rasterization resolution (>= 72 DPI; default 200).
        pages_spec: Optional 1-based page selection string (e.g. ``"1-4"``).
        max_pages: Maximum number of pages to render.
        file_prefix: Filename prefix (e.g. ``"src01_"``) for multi-source runs.

    Returns:
        Tuple ``(rendered_paths, total_pdf_pages, backend_used)``.

    Raises:
        FileNotFoundError: If ``pdf_path`` does not exist.
        ValueError: If ``dpi < 72``, the PDF header/trailer is invalid, or all
            requested pages exceed ``total_pdf_pages``.
        RuntimeError: If no PDF rasterization backend succeeds.
    """
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    if dpi < 72:
        raise ValueError(f"DPI must be >= 72 (got {dpi}).")
    head_bytes = pdf_path.read_bytes()[:1024]
    if b"%PDF-" not in head_bytes:
        raise ValueError(f"Corrupted or invalid PDF file '{pdf_path}': missing '%PDF-' header.")
    tail_bytes = pdf_path.read_bytes()[-2048:]
    if b"%%EOF" not in tail_bytes and b"endobj" not in tail_bytes:
        raise ValueError(f"Corrupted or truncated PDF file '{pdf_path}': missing trailer/EOF structure.")

    if selected_pages is not None:
        requested = [int(p) for p in selected_pages]
    elif pages_spec is not None and pages_spec.strip():
        requested = parse_page_selection(pages_spec, max(max_pages, 1) if max_pages > 0 else 10000)
    else:
        doc_pages, doc_texts = inspect_pdf_document(pdf_path)
        requested, _warn = select_pdf_pages_for_visual_plates(
            doc_pages, doc_texts, pages_spec, max_pages, pdf_path.name
        )
    pages_csv = ",".join(str(p) for p in requested)

    # 1. Primary macOS native PDFKit via /usr/bin/swift (zero third-party dependencies)
    swift_bin = shutil.which("swift")
    if swift_bin and sys.platform == "darwin":
        # Rasterize requested PDF pages to PNG plates at target DPI via macOS PDFKit.
        res = subprocess.run(
            [swift_bin, "-e", SWIFT_PDF_RENDERER, str(pdf_path), str(out_dir), str(dpi), pages_csv, file_prefix],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if res.returncode == 0 and res.stdout.strip():
            last_line = res.stdout.strip().splitlines()[-1]
            payload = json.loads(last_line)
            rendered = [Path(p) for p in payload.get("rendered", []) if Path(p).is_file()]
            total_pages = int(payload.get("total_pages", len(rendered)))
            if not rendered and total_pages > 0 and all(p > total_pages for p in requested):
                raise ValueError(
                    f"Requested pages {requested} exceed total page count ({total_pages}) of '{pdf_path.name}'."
                )
            if rendered:
                return (rendered, total_pages, "macos_pdfkit_swift")
        elif res.returncode != 0 and "Failed to open valid PDFDocument" in (res.stderr or ""):
            raise ValueError(f"Corrupted or unreadable PDF document '{pdf_path.name}'.")

    # 2. Cross-platform Poppler pdftoppm fallback
    pdftoppm_bin = shutil.which("pdftoppm")
    if pdftoppm_bin and requested:
        rendered_ppm: List[Path] = []
        # Rasterize PDF pages to PNG plates at target DPI using Poppler `pdftoppm`.
        for page_num in requested:
            prefix = out_dir / f"_ppm_{file_prefix}{page_num:03d}"
            # Rasterize PDF pages to PNG plates at target DPI using Poppler `pdftoppm`.
            res = subprocess.run(
                [
                    pdftoppm_bin,
                    "-jpeg",
                    "-jpegopt",
                    "quality=92",
                    "-r",
                    str(dpi),
                    "-f",
                    str(page_num),
                    "-l",
                    str(page_num),
                    "-singlefile",
                    str(pdf_path),
                    str(prefix),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            candidate = Path(f"{prefix}.jpg")
            if res.returncode == 0 and candidate.is_file():
                final_path = out_dir / f"{file_prefix}page_{page_num:03d}.jpg"
                candidate.replace(final_path)
                rendered_ppm.append(final_path)
        if rendered_ppm:
            return (rendered_ppm, len(rendered_ppm), "pdftoppm")

    raise RuntimeError(
        f"Unable to rasterize PDF '{pdf_path}'. Document is corrupted or no PDF backend succeeded."
    )


# ============================================================================
# Section 4: Approved HTML/Remote Figure Extraction & Safe Redirect Handling
# ============================================================================

def _select_best_srcset_candidate(srcset: str) -> str:
    """Pick the highest-resolution URL from an HTML srcset attribute."""
    best_url = ""
    best_score = -1.0
    for item in srcset.split(","):
        part = item.strip()
        if not part:
            continue
        tokens = part.split()
        url_cand = tokens[0].strip()
        score = 1.0
        if len(tokens) >= 2:
            desc = tokens[1].strip().lower()
            if desc.endswith("x"):
                try:
                    score = float(desc[:-1]) * 1000.0
                except ValueError:
                    score = 1.0
            elif desc.endswith("w"):
                try:
                    score = float(desc[:-1])
                except ValueError:
                    score = 1.0
        if score >= best_score:
            best_score = score
            best_url = url_cand
    return best_url


class _StructuredDocumentHTMLParser(html.parser.HTMLParser):
    """HTML parser extracting prose alongside ordered figures and PDF links.

    Attributes:
        text_parts: Accumulated Markdown-formatted prose fragments.
        figures: Ordered candidate figure descriptors (``img``, ``inline_svg``).
        pdf_links: Discovered ``.pdf`` href/embed/object targets.
    """

    # Helper function `__init__`: Initialize parser tracking state.
    def __init__(self) -> None:
        """Initialize parser tracking state."""
        super().__init__(convert_charrefs=True)
        self._skip_depth: int = 0
        self._in_svg: bool = False
        self._in_picture: bool = False
        self._picture_best_src: str = ""
        self._svg_chunks: List[str] = []
        self.text_parts: List[str] = []
        self.figures: List[Dict[str, object]] = []
        self.pdf_links: List[str] = []

    # Helper function `handle_starttag`: Record structural prose breaks, PDF links, and candidate figures.
    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        r"""Record structural prose breaks, PDF links, and candidate figures."""
        t = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        # Handle `t in ('script', 'style', 'noscript', 'template')`.
        if t in ("script", "style", "noscript", "template"):
            self._skip_depth += 1
            return
        # Handle `self._skip_depth > 0`.
        if self._skip_depth > 0:
            return

        # Handle `t == 'svg'`.
        if t == "svg":
            self._in_svg = True
            raw_attrs = " ".join(f'{k}="{v}"' for k, v in attrs if v is not None)
            self._svg_chunks = [f"<svg {raw_attrs}>"]
            return
        # Handle `self._in_svg`.
        if self._in_svg:
            raw_attrs = " ".join(f'{k}="{v}"' for k, v in attrs if v is not None)
            self._svg_chunks.append(f"<{tag} {raw_attrs}>".replace(" >", ">"))
            return

        # Handle `t == 'picture'`.
        if t == "picture":
            self._in_picture = True
            self._picture_best_src = ""
            return
        # Handle `t == 'source' and self._in_picture`.
        if t == "source" and self._in_picture:
            srcset_val = (attr_map.get("srcset") or attr_map.get("src") or "").strip()
            chosen = _select_best_srcset_candidate(srcset_val) if srcset_val else ""
            if chosen and not self._picture_best_src:
                self._picture_best_src = chosen
            return

        if t in ("h1", "h2", "h3", "h4"):
            level = int(t[1])
            self.text_parts.append("\n\n" + ("#" * level) + " ")
        elif t in ("p", "div", "section", "article", "tr", "blockquote", "pre", "figcaption"):
            self.text_parts.append("\n\n")
        elif t == "br":
            self.text_parts.append("\n")
        elif t == "li":
            self.text_parts.append("\n- ")
        elif t == "a":
            href = attr_map.get("href", "").strip()
            if href and urllib.parse.urlparse(href).path.lower().endswith(".pdf"):
                if href not in self.pdf_links:
                    self.pdf_links.append(href)
        elif t in ("embed", "iframe"):
            src_pdf = attr_map.get("src", "").strip()
            ctype = attr_map.get("type", "").lower()
            if src_pdf and ("pdf" in ctype or urllib.parse.urlparse(src_pdf).path.lower().endswith(".pdf")):
                if src_pdf not in self.pdf_links:
                    self.pdf_links.append(src_pdf)
        elif t == "object":
            data_ref = attr_map.get("data", "").strip()
            ctype = attr_map.get("type", "").lower()
            if data_ref:
                if "pdf" in ctype or urllib.parse.urlparse(data_ref).path.lower().endswith(".pdf"):
                    if data_ref not in self.pdf_links:
                        self.pdf_links.append(data_ref)
                elif (
                    "image/" in ctype
                    or "svg" in ctype
                    or urllib.parse.urlparse(data_ref).path.lower().endswith(
                        (".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif")
                    )
                ):
                    idx = len(self.figures) + 1
                    self.figures.append(
                        {
                            "kind": "img",
                            "index": idx,
                            "src": data_ref,
                            "alt": attr_map.get("title", "").strip() or "object_figure",
                            "title": attr_map.get("title", "").strip(),
                            "declared_width": _parse_int_px(attr_map.get("width")),
                            "declared_height": _parse_int_px(attr_map.get("height")),
                        }
                    )
                    self.text_parts.append(f"\n[FIGURE_CANDIDATE #{idx}: {data_ref}]\n")
        elif t == "img":
            srcset_best = _select_best_srcset_candidate(attr_map.get("srcset", "")) if attr_map.get("srcset") else ""
            raw_src = (
                attr_map.get("src")
                or attr_map.get("data-src")
                or attr_map.get("data-original")
                or ""
            ).strip()
            if self._in_picture and self._picture_best_src:
                src = self._picture_best_src
                self._picture_best_src = ""
            elif srcset_best and (not raw_src or "1x1" in raw_src or "placeholder" in raw_src.lower()):
                src = srcset_best
            else:
                src = raw_src or srcset_best
            # Handle `not src`.
            if not src:
                return
            alt = attr_map.get("alt", "").strip()
            title = attr_map.get("title", "").strip()
            w_val = _parse_int_px(attr_map.get("width"))
            h_val = _parse_int_px(attr_map.get("height"))
            idx = len(self.figures) + 1
            self.figures.append(
                {
                    "kind": "img",
                    "index": idx,
                    "src": src,
                    "alt": alt,
                    "title": title,
                    "declared_width": w_val,
                    "declared_height": h_val,
                }
            )
            label = alt or title or src
            self.text_parts.append(f"\n[FIGURE_CANDIDATE #{idx}: {label}]\n")

    # Helper function `handle_startendtag`: Delegate void HTML tags and inline SVG self-closing elements.
    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        r"""Delegate void HTML tags and inline SVG self-closing elements."""
        t = tag.lower()
        if self._skip_depth > 0:
            return
        # Handle `self._in_svg`.
        if self._in_svg:
            raw_attrs = " ".join(f'{k}="{v}"' for k, v in attrs if v is not None)
            self._svg_chunks.append(f"<{tag} {raw_attrs}/>".replace(" />", "/>"))
            return
        if t in ("img", "br", "source", "embed", "object"):
            self.handle_starttag(tag, attrs)

    # Helper function `handle_endtag`: Finalize skipped blocks, ``<picture>`` fallbacks, and inline SVGs.
    def handle_endtag(self, tag: str) -> None:
        r"""Finalize skipped blocks, ``<picture>`` fallbacks, and inline SVGs."""
        t = tag.lower()
        if t in ("script", "style", "noscript", "template") and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        # Handle `self._skip_depth > 0`.
        if self._skip_depth > 0:
            return
        if t == "picture" and self._in_picture:
            if self._picture_best_src:
                idx = len(self.figures) + 1
                self.figures.append(
                    {
                        "kind": "img",
                        "index": idx,
                        "src": self._picture_best_src,
                        "alt": "picture_source",
                        "title": "",
                        "declared_width": None,
                        "declared_height": None,
                    }
                )
                self.text_parts.append(f"\n[FIGURE_CANDIDATE #{idx}: {self._picture_best_src}]\n")
            self._in_picture = False
            self._picture_best_src = ""
            return
        if t == "svg" and self._in_svg:
            self._svg_chunks.append("</svg>")
            svg_xml = "".join(self._svg_chunks)
            self._in_svg = False
            self._svg_chunks = []
            idx = len(self.figures) + 1
            self.figures.append(
                {
                    "kind": "inline_svg",
                    "index": idx,
                    "svg_xml": svg_xml,
                    "alt": "inline_svg_diagram",
                }
            )
            self.text_parts.append(f"\n[FIGURE_CANDIDATE #{idx}: inline_svg_diagram]\n")
            return
        if self._in_svg:
            self._svg_chunks.append(f"</{tag}>")

    # Helper function `handle_data`: Append visible prose text or raw SVG child content.
    def handle_data(self, data: str) -> None:
        r"""Append visible prose text or raw SVG child content."""
        if self._skip_depth > 0:
            return
        # Handle `self._in_svg`.
        if self._in_svg:
            self._svg_chunks.append(data)
            return
        self.text_parts.append(data)


def _parse_int_px(raw: Optional[str]) -> Optional[int]:
    r"""Parse a leading positive pixel integer from an HTML dimension attribute.

    Args:
        raw: Raw attribute string (e.g. ``"320px"`` or ``"320"``).

    Returns:
        Parsed integer pixel count, or ``None`` if absent or non-numeric.
    """
    if not raw:
        return None
    m = re.match(r"^\s*(\d+)", raw)
    return int(m.group(1)) if m else None


class _ApprovedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler enforcing approved-domain checks on every 30x hop."""

    # Helper function `__init__`: Initialize the redirect handler with workspace domain policy.
    def __init__(self, cwd: Path, guards_mod: Optional[object], max_redirects: int = MAX_HTTP_REDIRECTS) -> None:
        """Initialize the redirect handler with workspace domain policy.

        Args:
            cwd: Workspace root directory used for domain registry resolution.
            guards_mod: Loaded ``pipeline_guards`` module.
            max_redirects: Maximum redirect hops permitted per request.
        """
        super().__init__()
        self._cwd = cwd
        self._guards_mod = guards_mod
        self._max_redirects = max_redirects
        self._hops = 0

    # Re-validate EVERY HTTP 301/302/307/308 redirect target against the approved
    # domain registry so an approved host cannot bounce a request onto localhost or
    # an unapproved third-party server (SSRF protection).
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        """Validate ``newurl`` against the registry before following a 30x hop."""
        self._hops += 1
        if self._hops > self._max_redirects:
            raise urllib.error.HTTPError(newurl, code, f"Exceeded {self._max_redirects} HTTP redirects.", headers, fp)
        resolved = urllib.parse.urljoin(req.full_url, newurl)
        ok, reason = _check_url_approved(resolved, self._cwd, self._guards_mod)
        if not ok:
            raise UnapprovedRedirectError(f"Blocked HTTP {code} redirect to unapproved target '{resolved}': {reason}")
        return super().redirect_request(req, fp, code, msg, headers, resolved)


def _http_get_bytes(
    url: str,
    cwd: Path,
    guards_mod: Optional[object],
    timeout: float = 30.0,
    http_fetcher: Optional[HttpFetcher] = None,
) -> Tuple[bytes, str]:
    """Fetch remote HTTP(S) content bounded by ``MAX_DOWNLOAD_BYTES``.

    Args:
        url: Approved remote HTTP(S) URL.
        cwd: Workspace root directory.
        guards_mod: Loaded ``pipeline_guards`` module.
        timeout: Socket timeout in seconds.
        http_fetcher: Optional test seam overriding network transport.

    Returns:
        Tuple ``(body_bytes, lowercased_content_type)``.

    Raises:
        ValueError: If response size exceeds ``MAX_DOWNLOAD_BYTES``.
    """
    if http_fetcher is not None:
        return http_fetcher(url, cwd, guards_mod)
    opener = urllib.request.build_opener(_ApprovedRedirectHandler(cwd=cwd, guards_mod=guards_mod))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=timeout) as resp:
        content_type = (resp.headers.get("Content-Type") or "").lower()
        body = resp.read(MAX_DOWNLOAD_BYTES + 1)
        if len(body) > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"Asset at '{url}' exceeds {MAX_DOWNLOAD_BYTES} byte safety ceiling.")
        return (body, content_type)


def _is_within_directory(candidate: Path, root: Path) -> bool:
    """Return ``True`` if ``candidate`` resolves inside ``root``.

    Args:
        candidate: Path to check for containment.
        root: Allowed ancestor directory.

    Returns:
        ``True`` when contained within ``root``, otherwise ``False``.
    """
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _decode_data_image_uri(src: str) -> Tuple[Optional[bytes], str, Optional[str]]:
    """Decode a ``data:image/*`` URI into raw bytes and an extension hint.

    Supports both base64 and URL-percent-encoded payloads (e.g. inline SVG data
    URIs).

    Args:
        src: Raw ``data:image/...`` URI string.

    Returns:
        Tuple ``(decoded_bytes, ext_hint, error_reason)``.
    """
    header, sep, payload = src.partition(",")
    if not sep or not payload:
        return (None, ".png", "Malformed data:image URI (missing comma payload).")
    h_lower = header.lower()
    if "image/svg" in h_lower:
        ext_hint = ".svg"
    elif "image/jpeg" in h_lower or "image/jpg" in h_lower:
        ext_hint = ".jpg"
    elif "image/webp" in h_lower:
        ext_hint = ".webp"
    elif "image/gif" in h_lower:
        ext_hint = ".gif"
    elif "image/bmp" in h_lower:
        ext_hint = ".bmp"
    elif "image/tiff" in h_lower:
        ext_hint = ".tiff"
    else:
        ext_hint = ".png"

    if ";base64" in h_lower:
        # Decode text bytes safely without failing on unexpected character encodings.
        try:
            return (base64.b64decode(payload, validate=False), ext_hint, None)
        except Exception as exc:
            return (None, ext_hint, f"Invalid base64 data URI: {exc}")
    # Handle URL-encoded or raw inline text data URIs (e.g. data:image/svg+xml).
    raw_bytes = urllib.parse.unquote_to_bytes(payload)
    if not raw_bytes:
        return (None, ext_hint, "Empty URL-encoded data URI payload.")
    return (raw_bytes, ext_hint, None)


def _stage_image_bytes(
    raw_bytes: bytes,
    ext_hint: str,
    stem: str,
    out_dir: Path,
    declared_w: Optional[int] = None,
    declared_h: Optional[int] = None,
) -> Tuple[Optional[Path], str]:
    r"""Validate, optionally transcode, and stage image bytes into ``out_dir``.

    Args:
        raw_bytes: Downloaded or decoded figure bytes.
        ext_hint: Filename suffix hint used to detect SVG inputs.
        stem: Output filename stem (without extension).
        out_dir: Staging directory path.
        declared_w: Optional HTML declared width in pixels.
        declared_h: Optional HTML declared height in pixels.

    Returns:
        Tuple ``(staged_path, status_reason)`` where ``status_reason`` is
        ``"ok"``, ``"decorative_icon"``, or ``"corrupted:<detail>"``.
    """
    if not raw_bytes:
        return (None, "corrupted:Empty image byte stream.")

    # Detect vector SVG figures (either by `.svg` suffix or `<svg` XML root) and
    # rasterize them to white-backed PNG plates so Gemma 4's Vision Encoder can read them.
    lowered_hint = ext_hint.lower()
    head_trimmed = raw_bytes[:1024].lstrip()
    is_svg = lowered_hint.endswith(".svg") or head_trimmed.lower().startswith((b"<svg", b"<?xml")) and b"<svg" in raw_bytes[:4096].lower()
    if is_svg:
        svg_text_head = raw_bytes.decode("utf-8", errors="replace")
        if "<svg" not in svg_text_head.lower() or "</svg>" not in svg_text_head.lower():
            return (None, "corrupted:Malformed or unclosed SVG XML payload.")
        # Create an isolated temporary directory that cleans itself up automatically.
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
            tmp.write(raw_bytes)
            tmp_svg = Path(tmp.name)
        try:
            # Inject the standard SVG XML namespace when inline HTML `<svg>` snippets omit it.
            if b"xmlns=" not in raw_bytes[:1024]:
                patched = re.sub(b"<svg\\b", b'<svg xmlns="http://www.w3.org/2000/svg"', raw_bytes, count=1, flags=re.IGNORECASE)
                tmp_svg.write_bytes(patched)
            out_png = out_dir / f"{stem}.png"
            if render_svg_to_png(tmp_svg, out_png):
                fmt, dims, err = validate_and_inspect_raster(out_png.read_bytes())
                if fmt != "png" or err is not None:
                    out_png.unlink(missing_ok=True)
                    return (None, f"corrupted:Rendered SVG failed PNG verification ({err}).")
                if is_decorative_icon(dims, declared_w, declared_h):
                    out_png.unlink(missing_ok=True)
                    return (None, "decorative_icon")
                return (out_png, "ok")
            return (None, "corrupted:SVG rasterization failed.")
        finally:
            tmp_svg.unlink(missing_ok=True)

    # Validate binary raster headers, CRC checksums, and pixel dimensions before staging.
    fmt, dims, err = validate_and_inspect_raster(raw_bytes)
    if fmt is None or err is not None:
        return (None, f"corrupted:{err or 'Unrecognized image format.'}")

    # Skip tiny nav icons and 1x1 tracking pixels while preserving wide equation strips.
    if is_decorative_icon(dims, declared_w, declared_h):
        return (None, "decorative_icon")

    # Convert legacy BMP/TIFF/GIF formats into standard PNG when `sips` is available.
    if fmt in ("bmp", "tiff", "gif"):
        out_png = out_dir / f"{stem}.png"
        if transcode_legacy_raster_to_png(raw_bytes, f".{fmt}", out_png):
            return (out_png, "ok")
        # Fallback if non-macOS without sips: preserve exact verified native extension (never mislabel as .png!)
        native_out = out_dir / f"{stem}.{fmt}"
        native_out.write_bytes(raw_bytes)
        return (native_out, "ok")

    suffix = f".{fmt}"
    out_path = out_dir / f"{stem}{suffix}"
    out_path.write_bytes(raw_bytes)
    return (out_path, "ok")


def extract_html_sources(
    html_text: str,
    base_ref: str,
    base_dir: Optional[Path],
    out_dir: Path,
    cwd: Path,
    guards_mod: Optional[object],
    file_prefix: str = "",
    max_figures: int = 16,
    http_fetcher: Optional[HttpFetcher] = None,
) -> Tuple[Path, List[Dict[str, object]], List[str], List[Dict[str, object]], List[str]]:
    r"""Extract normalized Markdown prose and stage figures from HTML.

    Enforces workspace path containment for local HTML references and domain
    registry checks for remote HTTP(S) images and PDF links.

    Args:
        html_text: Raw HTML document text.
        base_ref: Source file path or canonical URL for relative resolution.
        base_dir: Parent directory for local HTML assets, or ``None`` if remote.
        out_dir: Staging directory receiving PNG plates and ``web_text.md``.
        cwd: Active workspace root directory.
        guards_mod: Loaded ``pipeline_guards`` module.
        file_prefix: Filename prefix used to isolate multi-source batches.
        max_figures: Maximum number of non-decorative figures to stage.
        http_fetcher: Optional test seam overriding network transport.

    Returns:
        Tuple ``(web_text_path, staged_figures, skipped_unapproved,
        corrupted_assets, discovered_pdf_links)``.
    """
    parser = _StructuredDocumentHTMLParser()
    parser.feed(html_text)
    normalized_text = re.sub(r"[ \t]+", " ", "".join(parser.text_parts))
    normalized_text = re.sub(r"\n{3,}", "\n\n", normalized_text).strip()

    staged_figures: List[Dict[str, object]] = []
    skipped_unapproved: List[str] = []
    corrupted_assets: List[Dict[str, object]] = []
    discovered_pdf_links: List[str] = []
    fig_counter = 0

    containment_root: Optional[Path] = None
    if base_dir is not None:
        containment_root = cwd.resolve() if _is_within_directory(base_dir.resolve(), cwd.resolve()) else base_dir.resolve()

    for raw_pdf_link in parser.pdf_links:
        if base_ref.startswith(("http://", "https://")) or raw_pdf_link.startswith(("http://", "https://", "//")):
            resolved_pdf = urllib.parse.urljoin(base_ref, raw_pdf_link)
            ok, _ = _check_url_approved(resolved_pdf, cwd, guards_mod)
            if ok:
                discovered_pdf_links.append(resolved_pdf)
            else:
                skipped_unapproved.append(resolved_pdf)
        elif base_dir is not None and containment_root is not None:
            local_pdf = (base_dir / urllib.parse.unquote(raw_pdf_link)).resolve()
            if _is_within_directory(local_pdf, containment_root) and local_pdf.is_file():
                discovered_pdf_links.append(str(local_pdf))
            elif not _is_within_directory(local_pdf, containment_root):
                skipped_unapproved.append(raw_pdf_link)

    for fig in parser.figures:
        # Stop scanning further once `fig_counter >= max_figures`.
        if fig_counter >= max_figures:
            break
        kind = str(fig.get("kind", ""))
        cand_idx = int(fig.get("index", 0))
        declared_w = fig.get("declared_width") if isinstance(fig.get("declared_width"), int) else None
        declared_h = fig.get("declared_height") if isinstance(fig.get("declared_height"), int) else None

        if kind == "inline_svg":
            svg_xml = str(fig.get("svg_xml", ""))
            raw_bytes = svg_xml.encode("utf-8")
            staged_path, reason = _stage_image_bytes(
                raw_bytes,
                ".svg",
                f"{file_prefix}fig_{fig_counter + 1:03d}",
                out_dir,
                declared_w,
                declared_h,
            )
            if staged_path is not None:
                fig_counter += 1
                staged_figures.append(
                    {
                        "figure_index": fig_counter,
                        "candidate_index": cand_idx,
                        "path": str(staged_path.resolve()),
                        "alt": str(fig.get("alt", "")),
                        "origin": "inline_svg",
                    }
                )
                normalized_text = normalized_text.replace(
                    f"[FIGURE_CANDIDATE #{cand_idx}:",
                    f"[STAGED_FIGURE {staged_path.name} (candidate #{cand_idx}):",
                )
            elif reason.startswith("corrupted:"):
                detail = reason.split(":", 1)[1]
                corrupted_assets.append({"candidate_index": cand_idx, "origin": "inline_svg", "reason": detail})
                normalized_text = normalized_text.replace(
                    f"[FIGURE_CANDIDATE #{cand_idx}:",
                    f"[CORRUPTED_FIGURE #{cand_idx} ({detail}):",
                )
            continue

        src = str(fig.get("src", "")).strip()
        if not src:
            continue

        raw_bytes_opt: Optional[bytes] = None
        ext_hint = Path(urllib.parse.urlparse(src).path).suffix or ".png"
        origin_desc = src
        fetch_error: Optional[str] = None

        if src.startswith("data:image/"):
            raw_bytes_opt, ext_hint, fetch_error = _decode_data_image_uri(src)
            origin_desc = "data_uri"
        elif base_ref.startswith(("http://", "https://")) or src.startswith(("http://", "https://", "//")):
            resolved_url = urllib.parse.urljoin(base_ref, src)
            ok, _reason = _check_url_approved(resolved_url, cwd, guards_mod)
            if not ok:
                skipped_unapproved.append(resolved_url)
                continue
            try:
                raw_bytes_opt, ctype = _http_get_bytes(
                    resolved_url, cwd=cwd, guards_mod=guards_mod, http_fetcher=http_fetcher
                )
                if "svg" in ctype:
                    ext_hint = ".svg"
                origin_desc = resolved_url
            except UnapprovedRedirectError:
                skipped_unapproved.append(resolved_url)
                continue
            except Exception as exc:
                fetch_error = str(exc)
                origin_desc = resolved_url
        elif base_dir is not None and containment_root is not None:
            clean_rel = urllib.parse.unquote(urllib.parse.urlparse(src).path)
            local_img = (base_dir / clean_rel).resolve()
            if not _is_within_directory(local_img, containment_root):
                skipped_unapproved.append(src)
                continue
            if local_img.is_file():
                raw_bytes_opt = local_img.read_bytes()
                ext_hint = local_img.suffix or ".png"
                origin_desc = str(local_img)
            else:
                fetch_error = f"Missing local image file '{clean_rel}'."

        if raw_bytes_opt is None:
            if fetch_error:
                corrupted_assets.append({"candidate_index": cand_idx, "origin": origin_desc, "reason": fetch_error})
                normalized_text = normalized_text.replace(
                    f"[FIGURE_CANDIDATE #{cand_idx}:",
                    f"[CORRUPTED_FIGURE #{cand_idx} ({fetch_error}):",
                )
            continue

        staged_path, reason = _stage_image_bytes(
            raw_bytes_opt,
            ext_hint,
            f"{file_prefix}fig_{fig_counter + 1:03d}",
            out_dir,
            declared_w,
            declared_h,
        )
        if staged_path is not None:
            fig_counter += 1
            staged_figures.append(
                {
                    "figure_index": fig_counter,
                    "candidate_index": cand_idx,
                    "path": str(staged_path.resolve()),
                    "alt": str(fig.get("alt", "")),
                    "origin": origin_desc,
                }
            )
            normalized_text = normalized_text.replace(
                f"[FIGURE_CANDIDATE #{cand_idx}:",
                f"[STAGED_FIGURE {staged_path.name} (candidate #{cand_idx}):",
            )
        elif reason.startswith("corrupted:"):
            detail = reason.split(":", 1)[1]
            corrupted_assets.append({"candidate_index": cand_idx, "origin": origin_desc, "reason": detail})
            normalized_text = normalized_text.replace(
                f"[FIGURE_CANDIDATE #{cand_idx}:",
                f"[CORRUPTED_FIGURE #{cand_idx} ({detail}):",
            )

    per_src_web_md = out_dir / (f"{file_prefix}web_text.md" if file_prefix else "web_text.md")
    header_block = (
        f"# Extracted Web/HTML Text\n"
        f"- **Source:** `{base_ref}`\n"
        f"- **Staged Figures:** {len(staged_figures)}\n"
        f"- **Corrupted/Quarantined Assets:** {len(corrupted_assets)}\n\n"
    )
    section_body = header_block + normalized_text + "\n"
    per_src_web_md.write_text(section_body, encoding="utf-8")

    # Maintain combined web_text.md when file_prefix is active so @Scribe has a single merged prose entry point
    combined_web_md = out_dir / "web_text.md"
    if per_src_web_md != combined_web_md:
        if combined_web_md.is_file():
            prior = combined_web_md.read_text(encoding="utf-8")
            combined_web_md.write_text(prior.rstrip() + "\n\n---\n\n" + section_body, encoding="utf-8")
        else:
            combined_web_md.write_text(section_body, encoding="utf-8")

    return (combined_web_md, staged_figures, skipped_unapproved, corrupted_assets, discovered_pdf_links)


# ============================================================================
# Section 5: CLI Argument Parser & Visual Staging Entrypoint
# ============================================================================

def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for ``prepare_visual_sources.py``.

    Returns:
        Configured ``argparse.ArgumentParser`` instance.
    """
    parser = argparse.ArgumentParser(
        description="Stage PDF pages, SVG diagrams, local images, or approved web figures for @Scribe Vision OCR."
    )
    src_group = parser.add_mutually_exclusive_group(required=True)
    src_group.add_argument("--pdf", type=str, help="Local PDF file path to rasterize at 200 DPI.")
    src_group.add_argument("--html", type=str, help="Local HTML file path to extract prose and figure images from.")
    src_group.add_argument("--image", type=str, help="Local image or SVG path to normalize/stage for OCR.")
    src_group.add_argument("--dir", type=str, help="Local directory (e.g. 'research/') to batch-stage all PDFs, images, SVGs, and HTML files.")
    src_group.add_argument("--url", type=str, help="Approved remote HTTPS PDF, HTML, or image URL.")
    parser.add_argument("--pages", type=str, default=None, help="1-based page range for PDFs (e.g. '1-5,8').")
    parser.add_argument(
        "--max-pages",
        type=_parse_max_pages_arg,
        default=DEFAULT_MAX_PAGES,
        help="Visual plate warning cap for PDFs (default: 32; pass a higher integer or 'all'/'0' to override).",
    )
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI, help="Target rasterization DPI for PDFs (default: 200).")
    parser.add_argument("--out-dir", type=str, default=None, help="Optional override staging directory.")
    parser.add_argument("--append", action="store_true", help="Keep prior staged assets instead of clearing staging folder.")
    return parser


def main(argv: Optional[Sequence[str]] = None, http_fetcher: Optional[HttpFetcher] = None) -> int:
    """Stage visual sources into ``ocr_staging/`` and write ``staging_manifest.json``.

    Phases:
      - Phase 1: CLI Argument Parsing, Prior Manifest Merge & PDF Helper Setup
      - Phase 2: Modality Dispatch (`--pdf`, `--image`, `--html`, `--dir`, `--url`)
      - Phase 3: Manifest Persistence & Exit Status

    Args:
        argv: Optional CLI argument sequence overriding ``sys.argv[1:]``.
        http_fetcher: Optional HTTP transport callback for deterministic testing.

    Returns:
        ``0`` on success, ``2`` when blocked by the Approved Research Sources
        Registry, or ``1`` on validation/I/O error.
    """
    # --- Phase 1: CLI Argument Parsing, Prior Manifest Merge & PDF Helper Setup ---
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    cwd = Path.cwd().resolve()

    out_dir = Path(args.out_dir).resolve() if args.out_dir else (cwd / ".pipeline_scratch" / "ocr_staging")
    manifest_path = out_dir / "staging_manifest.json"

    prior_sources: List[Dict[str, object]] = []
    prior_images: List[str] = []
    prior_skipped: List[str] = []
    prior_corrupted: List[Dict[str, object]] = []
    prior_pdf_links: List[str] = []
    prior_web_text: Optional[str] = None

    if args.append and manifest_path.is_file():
        # Decode JSON content and fall back cleanly if the file or payload is malformed.
        try:
            prior_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(prior_manifest, dict):
                if isinstance(prior_manifest.get("sources"), list):
                    prior_sources = list(prior_manifest["sources"])
                elif prior_manifest.get("source"):
                    prior_sources = [
                        {
                            "source_index": 1,
                            "source_type": prior_manifest.get("source_type"),
                            "source": prior_manifest.get("source"),
                            "staged_images": list(prior_manifest.get("staged_images", [])),
                        }
                    ]
                prior_images = [str(p) for p in prior_manifest.get("staged_images", []) if Path(str(p)).is_file()]
                prior_skipped = [str(u) for u in prior_manifest.get("skipped_unapproved_urls", [])]
                prior_corrupted = list(prior_manifest.get("corrupted_assets", []))
                prior_pdf_links = [str(u) for u in prior_manifest.get("discovered_pdf_links", [])]
                if prior_manifest.get("web_text_path") and Path(str(prior_manifest["web_text_path"])).is_file():
                    prior_web_text = str(prior_manifest["web_text_path"])
        except Exception:
            pass
    elif out_dir.exists() and not args.append:
        shutil.rmtree(out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)
    plates_dir = out_dir / "plates"
    plates_dir.mkdir(parents=True, exist_ok=True)
    src_index = len(prior_sources) + 1
    file_prefix = f"src{src_index:02d}_" if (args.append or src_index > 1) else "src01_"
    out_in_cwd = _is_within_directory(out_dir, cwd)
    wrote_pdf_text_this_run = bool(args.append and (cwd / "SOURCE_TEXT.md").is_file())
    if out_in_cwd and not args.append and (cwd / "SOURCE_TEXT.md").is_file():
        (cwd / "SOURCE_TEXT.md").unlink(missing_ok=True)
    cap_warnings: List[str] = []

    def _stage_one_pdf(pdf_file: Path, disp_source: str, pref: str) -> Dict[str, object]:
        nonlocal wrote_pdf_text_this_run
        head_b = pdf_file.read_bytes()[:1024]
        tail_b = pdf_file.read_bytes()[-2048:]
        if b"%PDF-" not in head_b or (b"%%EOF" not in tail_b and b"endobj" not in tail_b):
            raise ValueError(f"Corrupted or invalid PDF file '{pdf_file}': missing PDF header or trailer.")
        doc_total, doc_texts = inspect_pdf_document(pdf_file)
        chosen_pages, warn_msg = select_pdf_pages_for_visual_plates(
            doc_total, doc_texts, args.pages, args.max_pages, Path(disp_source).name or disp_source
        )
        if warn_msg:
            cap_warnings.append(warn_msg)
            print(f"Warning: {warn_msg}", file=sys.stderr)
        rendered_p, total_p, backend_nm = render_pdf_to_pngs(
            pdf_file,
            out_dir,
            dpi=args.dpi,
            pages_spec=args.pages,
            max_pages=args.max_pages,
            file_prefix=pref,
            selected_pages=chosen_pages,
        )
        if out_in_cwd:
            wrote_path = persist_verbatim_source_text(
                cwd, disp_source, doc_texts, append=wrote_pdf_text_this_run
            )
            if wrote_path is not None:
                wrote_pdf_text_this_run = True
        entry_d: Dict[str, object] = {
            "dpi": args.dpi,
            "total_pages": total_p,
            "rendered_pages": chosen_pages,
            "backend": backend_nm,
            "staged_images": [str(p.resolve()) for p in rendered_p],
        }
        if warn_msg:
            entry_d["visual_page_cap_warning"] = warn_msg
        return entry_d

    guards_mod = _load_pipeline_guards(cwd)
    current_entry: Dict[str, object] = {"source_index": src_index}
    new_images: List[str] = []
    new_skipped: List[str] = []
    new_corrupted: List[Dict[str, object]] = []
    new_pdf_links: List[str] = []
    web_text_out: Optional[str] = prior_web_text

    # --- Phase 2: Modality Dispatch (`--pdf`, `--image`, `--html`, `--dir`, `--url`) ---
    try:
        if args.pdf:
            pdf_path = Path(args.pdf).expanduser().resolve()
            if not pdf_path.is_file():
                raise FileNotFoundError(f"PDF file does not exist: {pdf_path}")
            pdf_info = _stage_one_pdf(pdf_path, str(pdf_path), file_prefix)
            new_images = list(pdf_info["staged_images"])  # type: ignore[arg-type]
            current_entry.update({"source_type": "pdf", "source": str(pdf_path), **pdf_info})

        elif args.image:
            img_path = Path(args.image).expanduser().resolve()
            if not img_path.is_file():
                raise FileNotFoundError(f"Image file does not exist: {img_path}")
            staged, reason = _stage_image_bytes(
                img_path.read_bytes(), img_path.suffix, f"{file_prefix}img_001", out_dir
            )
            if staged is None:
                raise ValueError(f"Image '{img_path}' could not be staged ({reason}).")
            new_images = [str(staged.resolve())]
            current_entry.update(
                {
                    "source_type": "image",
                    "source": str(img_path),
                    "staged_images": new_images,
                }
            )

        elif args.html:
            html_path = Path(args.html).expanduser().resolve()
            if not html_path.is_file():
                raise FileNotFoundError(f"HTML file does not exist: {html_path}")
            html_text = html_path.read_text(encoding="utf-8", errors="replace")
            web_md, figs, skipped, corrupted, pdf_links = extract_html_sources(
                html_text,
                str(html_path),
                html_path.parent,
                out_dir,
                cwd,
                guards_mod,
                file_prefix=file_prefix,
                http_fetcher=http_fetcher,
            )
            new_images = [str(f["path"]) for f in figs]
            new_skipped = skipped
            new_corrupted = corrupted
            new_pdf_links = pdf_links
            web_text_out = str(web_md.resolve())
            current_entry.update(
                {
                    "source_type": "html",
                    "source": str(html_path),
                    "web_text_path": web_text_out,
                    "figures": figs,
                    "staged_images": new_images,
                    "corrupted_assets": corrupted,
                    "discovered_pdf_links": pdf_links,
                }
            )

        elif args.dir:
            dir_path = Path(args.dir).expanduser().resolve()
            if not dir_path.is_dir():
                raise FileNotFoundError(f"Source directory does not exist: {dir_path}")
            visual_exts = {
                ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".svg", ".html", ".htm"
            }
            candidates = [
                p for p in sorted(dir_path.rglob("*"))
                if p.is_file()
                and not any(part.startswith(".") for part in p.relative_to(dir_path).parts)
                and p.suffix.lower() in visual_exts
            ]
            if not candidates:
                raise ValueError(f"No visual, PDF, SVG, or HTML files found in directory: {dir_path}")

            dir_entries: List[Dict[str, object]] = []
            next_idx = src_index
            for cand in candidates:
                cand_prefix = f"src{next_idx:02d}_"
                ext_low = cand.suffix.lower()
                try:
                    if ext_low == ".pdf":
                        pdf_info_d = _stage_one_pdf(cand, str(cand), cand_prefix)
                        c_imgs = list(pdf_info_d["staged_images"])  # type: ignore[arg-type]
                        new_images.extend(c_imgs)
                        dir_entries.append(
                            {
                                "source_index": next_idx,
                                "source_type": "pdf",
                                "source": str(cand),
                                **pdf_info_d,
                            }
                        )
                        next_idx += 1
                    elif ext_low in (".html", ".htm"):
                        html_text = cand.read_text(encoding="utf-8", errors="replace")
                        web_md, figs, skipped, corrupted, pdf_links = extract_html_sources(
                            html_text,
                            str(cand),
                            cand.parent,
                            out_dir,
                            cwd,
                            guards_mod,
                            file_prefix=cand_prefix,
                            http_fetcher=http_fetcher,
                        )
                        c_imgs = [str(f["path"]) for f in figs]
                        new_images.extend(c_imgs)
                        new_skipped.extend(skipped)
                        new_corrupted.extend(corrupted)
                        new_pdf_links.extend(pdf_links)
                        web_text_out = str(web_md.resolve())
                        dir_entries.append(
                            {
                                "source_index": next_idx,
                                "source_type": "html",
                                "source": str(cand),
                                "web_text_path": web_text_out,
                                "figures": figs,
                                "staged_images": c_imgs,
                                "corrupted_assets": corrupted,
                                "discovered_pdf_links": pdf_links,
                            }
                        )
                        next_idx += 1
                    else:
                        staged, reason = _stage_image_bytes(
                            cand.read_bytes(), cand.suffix, f"{cand_prefix}img_001", out_dir
                        )
                        if staged is not None:
                            s_abs = str(staged.resolve())
                            new_images.append(s_abs)
                            dir_entries.append(
                                {
                                    "source_index": next_idx,
                                    "source_type": "image",
                                    "source": str(cand),
                                    "staged_images": [s_abs],
                                }
                            )
                            next_idx += 1
                        elif reason.startswith("corrupted:"):
                            new_corrupted.append({"origin": str(cand), "reason": reason.split(":", 1)[1]})
                except Exception as item_exc:
                    new_corrupted.append({"origin": str(cand), "reason": str(item_exc)})

            if not new_images and not web_text_out:
                raise ValueError(
                    f"Directory '{dir_path}' produced zero valid staged visual or HTML assets ({len(new_corrupted)} corrupted)."
                )
            prior_sources.extend(dir_entries[:-1] if dir_entries else [])
            if dir_entries:
                current_entry = dir_entries[-1]
            else:
                current_entry.update({"source_type": "directory", "source": str(dir_path), "staged_images": []})

        elif args.url:
            target_url = args.url.strip()
            ok, reason = _check_url_approved(target_url, cwd, guards_mod)
            if not ok:
                err_payload = {"status": "blocked_unapproved_url", "url": target_url, "error": reason}
                print(json.dumps(err_payload, indent=2))
                return 2

            try:
                raw_bytes, ctype = _http_get_bytes(
                    target_url, cwd=cwd, guards_mod=guards_mod, http_fetcher=http_fetcher
                )
            except UnapprovedRedirectError as redir_exc:
                err_payload = {"status": "blocked_unapproved_url", "url": target_url, "error": str(redir_exc)}
                print(json.dumps(err_payload, indent=2))
                return 2

            url_path = urllib.parse.urlparse(target_url).path.lower()
            is_pdf = "application/pdf" in ctype or url_path.endswith(".pdf") or raw_bytes[:5] == b"%PDF-"
            is_img = ctype.startswith("image/") or url_path.endswith(
                (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp", ".tiff", ".tif")
            )

            if is_pdf:
                dl_pdf = out_dir / f"{file_prefix}remote_source.pdf"
                dl_pdf.write_bytes(raw_bytes)
                try:
                    pdf_info_r = _stage_one_pdf(dl_pdf, target_url, file_prefix)
                finally:
                    dl_pdf.unlink(missing_ok=True)
                new_images = list(pdf_info_r["staged_images"])  # type: ignore[arg-type]
                current_entry.update(
                    {
                        "source_type": "remote_pdf",
                        "source": target_url,
                        **pdf_info_r,
                    }
                )
            elif is_img:
                ext_hint = Path(url_path).suffix or (".svg" if "svg" in ctype else ".png")
                staged, stage_reason = _stage_image_bytes(raw_bytes, ext_hint, f"{file_prefix}fig_001", out_dir)
                if staged is None and stage_reason.startswith("corrupted:"):
                    raise ValueError(f"Remote image '{target_url}' is corrupted ({stage_reason}).")
                new_images = [str(staged.resolve())] if staged else []
                current_entry.update(
                    {
                        "source_type": "remote_image",
                        "source": target_url,
                        "staged_images": new_images,
                    }
                )
            else:
                html_text = raw_bytes.decode("utf-8", errors="replace")
                web_md, figs, skipped, corrupted, pdf_links = extract_html_sources(
                    html_text,
                    target_url,
                    None,
                    out_dir,
                    cwd,
                    guards_mod,
                    file_prefix=file_prefix,
                    http_fetcher=http_fetcher,
                )
                new_images = [str(f["path"]) for f in figs]
                new_skipped = skipped
                new_corrupted = corrupted
                new_pdf_links = pdf_links
                web_text_out = str(web_md.resolve())
                current_entry.update(
                    {
                        "source_type": "remote_html",
                        "source": target_url,
                        "web_text_path": web_text_out,
                        "figures": figs,
                        "staged_images": new_images,
                        "corrupted_assets": corrupted,
                        "discovered_pdf_links": pdf_links,
                    }
                )

        # --- Phase 3: Manifest Persistence & Exit Status ---
        merged_images = list(dict.fromkeys(prior_images + new_images))
        merged_skipped = list(dict.fromkeys(prior_skipped + new_skipped))
        merged_pdf_links = list(dict.fromkeys(prior_pdf_links + new_pdf_links))
        merged_sources = prior_sources + [current_entry]
        merged_corrupted = prior_corrupted + new_corrupted

        manifest: Dict[str, object] = dict(current_entry)
        manifest.update(
            {
                "status": "ok",
                "staging_dir": str(out_dir),
                "ocr_plates_dir": str(plates_dir.resolve()),
                "source_count": len(merged_sources),
                "sources": merged_sources,
                "staged_images": merged_images,
                "web_text_path": web_text_out,
                "skipped_unapproved_urls": merged_skipped,
                "corrupted_assets": merged_corrupted,
                "discovered_pdf_links": merged_pdf_links,
                "visual_page_cap_warnings": cap_warnings,
            }
        )

        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(manifest, indent=2))
        return 0
    except Exception as exc:
        err_payload = {"status": "error", "error": str(exc), "staging_dir": str(out_dir)}
        print(json.dumps(err_payload, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
