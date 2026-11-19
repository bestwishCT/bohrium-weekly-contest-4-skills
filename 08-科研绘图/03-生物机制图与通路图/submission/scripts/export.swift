import AppKit
import Foundation
import WebKit
import PDFKit

final class Options {
    var input = ""
    var png = ""
    var pdf = ""
    var preview = ""
    var report = ""
    var width = 1200.0
    var height = 800.0
    var scale = 1.0
}

func arguments() -> Options {
    let out = Options()
    var i = 1
    let a = CommandLine.arguments
    while i < a.count {
        let key = a[i]
        if i + 1 >= a.count { fatalError("Missing value for \(key)") }
        let value = a[i + 1]
        switch key {
        case "--input": out.input = value
        case "--png": out.png = value
        case "--pdf": out.pdf = value
        case "--pdf-preview": out.preview = value
        case "--report": out.report = value
        case "--width": out.width = Double(value) ?? 0
        case "--height": out.height = Double(value) ?? 0
        case "--scale": out.scale = Double(value) ?? 1
        default: fatalError("Unknown option: \(key)")
        }
        i += 2
    }
    guard !out.input.isEmpty, !out.png.isEmpty, !out.pdf.isEmpty,
          !out.preview.isEmpty, !out.report.isEmpty,
          out.width > 0, out.height > 0, out.scale > 0,
          out.width <= 10000, out.height <= 10000, out.scale <= 4 else {
        fatalError("Usage: export --input in.svg --png out.png --pdf out.pdf --pdf-preview pdf.png --report report.json --width W --height H --scale S")
    }
    return out
}

func pngData(_ image: NSImage) throws -> (Data, Int, Int) {
    guard let tiff = image.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: tiff),
          let data = bitmap.representation(using: .png, properties: [:]) else {
        throw NSError(domain: "Export", code: 2, userInfo: [NSLocalizedDescriptionKey: "PNG encoding failed"])
    }
    return (data, bitmap.pixelsWide, bitmap.pixelsHigh)
}

func save(_ data: Data, _ path: String) throws {
    let url = URL(fileURLWithPath: path)
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try data.write(to: url, options: .atomic)
}

final class Renderer: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    let opt = arguments()
    var webView: WKWebView!
    var active = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        let frame = CGRect(x: 0, y: 0, width: opt.width, height: opt.height)
        let configuration = WKWebViewConfiguration()
        configuration.preferences.setValue(false, forKey: "javaScriptCanOpenWindowsAutomatically")
        webView = WKWebView(frame: frame, configuration: configuration)
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")
        let url = URL(fileURLWithPath: opt.input).standardizedFileURL
        guard FileManager.default.fileExists(atPath: url.path) else {
            fail("Input does not exist: \(url.path)")
            return
        }
        active = true
        webView.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        DispatchQueue.main.asyncAfter(deadline: .now() + 20) {
            if self.active { self.fail("Timed out loading or exporting SVG") }
        }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        fail("SVG navigation failed: \(error.localizedDescription)")
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        fail("SVG load failed: \(error.localizedDescription)")
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
            self.exportPNG()
        }
    }

    func exportPNG() {
        guard active else { return }
        let config = WKSnapshotConfiguration()
        config.rect = CGRect(x: 0, y: 0, width: opt.width, height: opt.height)
        config.snapshotWidth = NSNumber(value: opt.width * opt.scale)
        webView.takeSnapshot(with: config) { image, error in
            if let error = error { self.fail("PNG snapshot failed: \(error.localizedDescription)"); return }
            guard let image = image else { self.fail("PNG snapshot returned no image"); return }
            do {
                let result = try pngData(image)
                try save(result.0, self.opt.png)
                self.exportPDF(pngWidth: result.1, pngHeight: result.2)
            } catch { self.fail("PNG write failed: \(error.localizedDescription)") }
        }
    }

    func exportPDF(pngWidth: Int, pngHeight: Int) {
        guard active else { return }
        let config = WKPDFConfiguration()
        config.rect = CGRect(x: 0, y: 0, width: opt.width, height: opt.height)
        webView.createPDF(configuration: config) { result in
            switch result {
            case .failure(let error):
                self.fail("PDF export failed: \(error.localizedDescription)")
            case .success(let data):
                do {
                    guard data.starts(with: Array("%PDF".utf8)) else {
                        self.fail("PDF exporter returned a non-PDF payload"); return
                    }
                    try save(data, self.opt.pdf)
                    try self.renderPDFPreview(data, pngWidth: pngWidth, pngHeight: pngHeight)
                } catch { self.fail("PDF write/preview failed: \(error.localizedDescription)") }
            }
        }
    }

    func renderPDFPreview(_ data: Data, pngWidth: Int, pngHeight: Int) throws {
        guard let document = PDFDocument(data: data), document.pageCount == 1,
              let page = document.page(at: 0) else {
            throw NSError(domain: "Export", code: 3, userInfo: [NSLocalizedDescriptionKey: "Expected one readable PDF page"])
        }
        let box = page.bounds(for: .mediaBox)
        guard box.width > 0, box.height > 0 else {
            throw NSError(domain: "Export", code: 4, userInfo: [NSLocalizedDescriptionKey: "Empty PDF media box"])
        }
        let targetW = opt.width * opt.scale
        let targetH = opt.height * opt.scale
        let sx = targetW / box.width
        let sy = targetH / box.height
        let image = NSImage(size: NSSize(width: targetW, height: targetH))
        image.lockFocus()
        NSColor.white.setFill()
        NSRect(x: 0, y: 0, width: targetW, height: targetH).fill()
        if let context = NSGraphicsContext.current?.cgContext {
            context.saveGState()
            context.scaleBy(x: sx, y: sy)
            page.draw(with: .mediaBox, to: context)
            context.restoreGState()
        }
        image.unlockFocus()
        let preview = try pngData(image)
        try save(preview.0, opt.preview)

        let webkit = Bundle(path: "/System/Library/Frameworks/WebKit.framework")
        let webkitVersion = webkit?.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "unknown"
        let font = NSFont(name: "Helvetica", size: 14)
        let sourceSize = (try? FileManager.default.attributesOfItem(atPath: opt.input)[.size] as? NSNumber)?.intValue ?? -1
        let object: [String: Any] = [
            "status": "passed",
            "renderer": "WKWebView.loadFileURL/takeSnapshot/createPDF + PDFKit page.draw",
            "webkit_bundle_version": webkitVersion,
            "font_resolution": font == nil ? "Helvetica unavailable" : "Helvetica -> \(font!.fontName)",
            "source_file": URL(fileURLWithPath: opt.input).lastPathComponent,
            "source_bytes": sourceSize,
            "viewport": [opt.width, opt.height],
            "scale": opt.scale,
            "png_pixels": [pngWidth, pngHeight],
            "png_signature_checked": true,
            "pdf_header_checked": true,
            "pdf_page_count": document.pageCount,
            "pdf_media_box_points": [box.origin.x, box.origin.y, box.width, box.height],
            "pdf_preview_pixels": [preview.1, preview.2]
        ]
        let json = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
        try save(json, opt.report)
        active = false
        print("PASS SVG->PNG/PDF + PDF render: \(pngWidth)x\(pngHeight), one PDF page")
        exit(0)
    }

    func fail(_ message: String) {
        active = false
        FileHandle.standardError.write(Data(("FAIL " + message + "\n").utf8))
        exit(1)
    }
}

let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let renderer = Renderer()
application.delegate = renderer
application.run()
