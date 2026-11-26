    // ======================================================================
    // 预览对照辅助：离线 docx-preview 加载器 + 按题目锚点匹配与同步插值
    // ======================================================================
    var _docxPreviewLibPromise = null;
    function loadDocxPreviewLib() {
      if (typeof window === 'undefined') return Promise.reject(new Error('当前环境不支持浏览器渲染'));
      if (window.JSZip && window.docx && typeof window.docx.renderAsync === 'function') {
        return Promise.resolve(window.docx);
      }
      if (_docxPreviewLibPromise) return _docxPreviewLibPromise;
      const runVendorScript = async (filename) => {
        const urls = [API + '/vendor/' + filename, '/wf-plugin/vendor/' + filename];
        let code = '';
        let loadedUrl = '';
        let lastErr = '';
        for (const url of urls) {
          try {
            const res = await window.fetch(url, { cache: 'force-cache' });
            if (res.ok) {
              code = await res.text();
              loadedUrl = url;
              break;
            }
            lastErr = url + ' (' + res.status + ')';
          } catch (e) {
            lastErr = url + ' (' + String((e && e.message) || e) + ')';
          }
        }
        if (!code) throw new Error('加载 Word 渲染组件失败：' + lastErr);
        try {
          // Force UMD browser-global branch so window.JSZip / window.docx are attached cleanly.
          const fn = new Function('window', 'globalThis', 'self', 'exports', 'module', 'define', code);
          fn(window, window, window, undefined, undefined, undefined);
          return;
        } catch (evalErr) {
          // Fallback to <script src> if CSP disallows new Function.
          if (typeof document === 'undefined' || !loadedUrl) throw evalErr;
          await new Promise((resolve, reject) => {
            const s = document.createElement('script');
            s.src = loadedUrl;
            s.async = false;
            s.onload = () => resolve();
            s.onerror = () => reject(new Error('脚本加载失败：' + loadedUrl));
            (document.head || document.documentElement).appendChild(s);
          });
        }
      };
      _docxPreviewLibPromise = (async () => {
        if (!window.JSZip) await runVendorScript('jszip.min.js');
        if (!window.docx || typeof window.docx.renderAsync !== 'function') {
          await runVendorScript('docx-preview.min.js');
        }
        if (!window.docx || typeof window.docx.renderAsync !== 'function') {
          throw new Error('Word 渲染组件初始化失败');
        }
        return window.docx;
      })().catch((err) => {
        _docxPreviewLibPromise = null;
        throw err;
      });
      return _docxPreviewLibPromise;
    }

    function base64ToUint8Array(b64) {
      const bin = atob(String(b64 || ''));
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return bytes;
    }

    function normalizeCompareText(s) {
      return String(s || '')
        .replace(/\[IMG\s+[^\]]+\]/gi, ' ')
        .replace(/\[TABLE\s+[^\]]+\]/gi, ' ')
        .replace(/<[^>]+>/g, ' ')
        .replace(/\*{1,3}/g, '')
        .replace(/[\s\u3000\xa0，,。．.；;：:！!？?（）()【】\[\]《》“”"'‘’`\-—–_·•/\\|]+/g, '')
        .toLowerCase();
    }

    function buildCompareSearchProfile(it, meta) {
      const phrases = [];
      const seen = new Set();
      const pushPhrase = (raw, weight) => {
        const lines = String(raw || '').replace(/\r\n?/g, '\n').split('\n');
        for (const line of lines) {
          const cleaned = line
            .replace(/\[IMG\s+[^\]]+\]/gi, ' ')
            .replace(/\[TABLE\s+[^\]]+\]/gi, ' ')
            .replace(/<[^>]+>/g, ' ')
            .trim();
          const norm = normalizeCompareText(cleaned);
          if (norm.length < 4) continue;
          const head = norm.slice(0, 32);
          if (seen.has(head)) continue;
          seen.add(head);
          phrases.push({ text: head, weight: weight || 1 });
          if (phrases.length >= 6) return;
        }
      };
      if (meta && meta.secChanged && meta.secTitle) pushPhrase(meta.secTitle, 3);
      if (meta && meta.showSecInst && meta.secInst) pushPhrase(meta.secInst, 2);
      if (it && it.material) pushPhrase(it.material, 3);
      if (it && it.stem) pushPhrase(it.stem, 3);
      if (it && Array.isArray(it.subquestions)) {
        for (let i = 0; i < Math.min(2, it.subquestions.length); i++) {
          const sub = it.subquestions[i];
          if (sub && sub.stem) pushPhrase(sub.stem, 2);
        }
      }
      if (it && Array.isArray(it.options) && it.options.length) {
        const firstOpt = typeof it.options[0] === 'string'
          ? it.options[0]
          : (it.options[0] && it.options[0].text ? it.options[0].text : '');
        if (firstOpt) pushPhrase(firstOpt, 1);
      }
      const numbers = [];
      const addNum = (n) => {
        if (n === null || n === undefined) return;
        const s = String(n).trim();
        if (/^\d{1,3}$/.test(s) && !numbers.includes(s)) numbers.push(s);
      };
      if (it) {
        addNum(it.original_number);
        addNum(it.number);
        if (Array.isArray(it.numbers)) it.numbers.forEach(addNum);
        if (Array.isArray(it.subquestions) && it.subquestions.length) {
          addNum(it.subquestions[0].original_number);
          addNum(it.subquestions[0].number);
        }
      }
      return { phrases, numbers };
    }

    function matchCompareAnchors(profiles, rightBlocks) {
      if (!Array.isArray(profiles) || !profiles.length || !Array.isArray(rightBlocks) || !rightBlocks.length) {
        return [];
      }
      // Detect trailing answer/key appendix so question numbers do not accidentally anchor into the answer key.
      let searchEnd = rightBlocks.length;
      const appendixStart = Math.floor(rightBlocks.length * 0.52);
      for (let bi = appendixStart; bi < rightBlocks.length; bi++) {
        const raw = String(rightBlocks[bi].raw || '').trim();
        if (raw.length <= 24 && /^(?:参考答案|答案与解析|参考答案及解析|参考答案与解析|听力原文|答案速查|answer\s*key|keys?)(?:[\s：:（(].*)?$/i.test(raw)) {
          searchEnd = bi;
          break;
        }
      }
      const anchors = [];
      let cursor = 0;
      const matchNumberAtStart = (raw, numStr) => {
        const esc = numStr.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        return new RegExp('^(?:[（(]\\s*[）)]\\s*)?' + esc + '(?:[\\.．、\\)）\\s\\u3000]|$)').test(raw);
      };
      for (let idx = 0; idx < profiles.length; idx++) {
        const prof = profiles[idx] || { phrases: [], numbers: [] };
        let bestBi = -1;
        let bestScore = 0;
        const windowEnd = searchEnd;
        // Pass 1: phrase match from cursor onward (strongly prefer earliest strong match to stay monotonic).
        for (let bi = cursor; bi < windowEnd; bi++) {
          const norm = rightBlocks[bi].norm;
          if (!norm) continue;
          let score = 0;
          for (const ph of prof.phrases) {
            const needle = ph.text;
            const probe = needle.length > 14 ? needle.slice(0, 14) : needle;
            if (norm.includes(needle)) {
              score = Math.max(score, 100 * ph.weight + needle.length);
            } else if (probe.length >= 6 && norm.includes(probe)) {
              score = Math.max(score, 70 * ph.weight + probe.length);
            } else if (norm.length >= 8 && needle.includes(norm.slice(0, Math.min(norm.length, 16)))) {
              score = Math.max(score, 55 * ph.weight + norm.length);
            }
          }
          if (score > bestScore) {
            bestScore = score;
            bestBi = bi;
            if (score >= 160) break;
          }
        }
        // Pass 2: fallback to printed question number at start of paragraph/row.
        if (bestBi < 0 && prof.numbers.length) {
          const targetNum = prof.numbers[0];
          for (let bi = cursor; bi < windowEnd; bi++) {
            const raw = String(rightBlocks[bi].raw || '').trim();
            if (matchNumberAtStart(raw, targetNum)) {
              bestBi = bi;
              break;
            }
          }
        }
        if (bestBi >= 0) {
          anchors.push({ idx, blockIdx: bestBi });
          cursor = Math.min(windowEnd, bestBi + 1);
        }
      }
      return anchors;
    }

    function interpolateSyncedScroll(srcPos, pairs, srcMax, dstMax) {
      const sMax = Math.max(0, Number(srcMax) || 0);
      const dMax = Math.max(0, Number(dstMax) || 0);
      if (sMax <= 0 || dMax <= 0) return 0;
      const clamped = Math.max(0, Math.min(sMax, Number(srcPos) || 0));
      const pts = [{ src: 0, dst: 0 }];
      if (Array.isArray(pairs)) {
        for (const p of pairs) {
          if (!p || !Number.isFinite(p.src) || !Number.isFinite(p.dst)) continue;
          const prev = pts[pts.length - 1];
          const sx = Math.max(prev.src, Math.min(sMax, p.src));
          const dx = Math.max(prev.dst, Math.min(dMax, p.dst));
          if (sx > prev.src) pts.push({ src: sx, dst: dx });
        }
      }
      const last = pts[pts.length - 1];
      if (last.src < sMax) pts.push({ src: sMax, dst: dMax });
      if (pts.length < 2) return (clamped / sMax) * dMax;
      for (let i = 0; i < pts.length - 1; i++) {
        const a = pts[i];
        const b = pts[i + 1];
        if (clamped >= a.src && clamped <= b.src) {
          const span = b.src - a.src;
          const t = span > 0 ? (clamped - a.src) / span : 0;
          return Math.max(0, Math.min(dMax, a.dst + t * (b.dst - a.dst)));
        }
      }
      return dMax;
    }

    function PaperPreviewModal(props) {
      const React = require('react');
      const h = React.createElement;
      const { batchName, data, loading, error, onClose, onReload } = props;
      const [showKey, setShowKey] = React.useState(false);
      const [syncScroll, setSyncScroll] = React.useState(true);
      const [docxStatus, setDocxStatus] = React.useState('loading'); // loading | ready | missing | error
      const [docxMeta, setDocxMeta] = React.useState(null);
      const [docxError, setDocxError] = React.useState('');
      const [docxSeq, setDocxSeq] = React.useState(0);
      const [anchorStats, setAnchorStats] = React.useState({ matched: 0, total: 0 });
      const leftScrollRef = React.useRef(null);
      const rightScrollRef = React.useRef(null);
      const docxBodyRef = React.useRef(null);
      const docxStyleRef = React.useRef(null);
      const leftItemRefs = React.useRef([]);
      const anchorsRef = React.useRef([]);
      const syncLockRef = React.useRef({ driver: '', until: 0 });
      const rafRef = React.useRef(0);
      const flashTimerRef = React.useRef(null);
      const flashElRef = React.useRef(null);
      const itemProfilesRef = React.useRef([]);

      React.useEffect(() => {
        let cancelled = false;
        setDocxStatus('loading');
        setDocxError('');
        setDocxMeta(null);
        anchorsRef.current = [];
        setAnchorStats({ matched: 0, total: Array.isArray(data?.items) ? data.items.length : 0 });
        (async () => {
          try {
            const res = await jget('/batches/' + encodeURIComponent(batchName) + '/source-docx');
            if (cancelled) return;
            if (!res || !res.ok || !res.base64) {
              setDocxStatus('missing');
              setDocxError((res && res.reason) || '该批次未附带 Word（.docx）原文件');
              return;
            }
            setDocxMeta({ filename: res.filename || 'source.docx', size: res.size || 0 });
            const docxLib = await loadDocxPreviewLib();
            if (cancelled) return;
            const host = docxBodyRef.current;
            const styleHost = docxStyleRef.current;
            if (!host) return;
            host.innerHTML = '';
            if (styleHost) styleHost.innerHTML = '';
            const bytes = base64ToUint8Array(res.base64);
            const buf = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
            await docxLib.renderAsync(buf, host, styleHost || undefined, {
              className: 'wf-docx',
              inWrapper: true,
              ignoreWidth: true,
              ignoreHeight: true,
              breakPages: true,
              useBase64URL: true,
              ignoreLastRenderedPageBreak: true,
              renderHeaders: true,
              renderFooters: true,
            });
            if (cancelled) return;
            setDocxStatus('ready');
          } catch (e) {
            if (cancelled) return;
            setDocxStatus('error');
            setDocxError(String((e && e.message) || e || 'Word 原文件渲染失败'));
          }
        })();
        return () => {
          cancelled = true;
          if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = 0; }
          if (flashTimerRef.current) { clearTimeout(flashTimerRef.current); flashTimerRef.current = null; }
        };
      }, [batchName, docxSeq]);

      const rebuildAnchors = React.useCallback(() => {
        const host = docxBodyRef.current;
        const profiles = itemProfilesRef.current || [];
        if (!host || docxStatus !== 'ready' || !profiles.length) {
          anchorsRef.current = [];
          setAnchorStats({ matched: 0, total: profiles.length });
          return;
        }
        const rawNodes = host.querySelectorAll('section.wf-docx p, section.wf-docx tr, section.wf-docx h1, section.wf-docx h2, section.wf-docx h3, section.wf-docx h4, section.wf-docx li');
        const blocks = [];
        for (let i = 0; i < rawNodes.length; i++) {
          const el = rawNodes[i];
          if (el.tagName === 'P' && el.closest('tr')) continue;
          const raw = (el.textContent || '').replace(/\s+/g, ' ').trim();
          const norm = normalizeCompareText(raw);
          if (!raw && !norm) continue;
          blocks.push({ el, raw, norm });
        }
        const matched = matchCompareAnchors(profiles, blocks);
        anchorsRef.current = matched.map((m) => ({ idx: m.idx, rightEl: blocks[m.blockIdx].el }));
        setAnchorStats({ matched: matched.length, total: profiles.length });
      }, [docxStatus]);

      React.useEffect(() => {
        if (docxStatus === 'ready' && !loading) {
          const t = setTimeout(rebuildAnchors, 30);
          return () => clearTimeout(t);
        }
      }, [docxStatus, loading, data, rebuildAnchors]);

      const relativeTop = (el, container) => {
        if (!el || !container) return 0;
        const er = el.getBoundingClientRect();
        const cr = container.getBoundingClientRect();
        return er.top - cr.top + container.scrollTop;
      };

      const handlePaneScroll = (driver) => {
        if (!syncScroll || docxStatus !== 'ready') return;
        const now = Date.now();
        if (syncLockRef.current.driver && syncLockRef.current.driver !== driver && now < syncLockRef.current.until) {
          return;
        }
        syncLockRef.current = { driver, until: now + 150 };
        if (rafRef.current) cancelAnimationFrame(rafRef.current);
        rafRef.current = requestAnimationFrame(() => {
          rafRef.current = 0;
          const leftEl = leftScrollRef.current;
          const rightEl = rightScrollRef.current;
          if (!leftEl || !rightEl) return;
          const srcEl = driver === 'left' ? leftEl : rightEl;
          const dstEl = driver === 'left' ? rightEl : leftEl;
          const srcMax = Math.max(0, srcEl.scrollHeight - srcEl.clientHeight);
          const dstMax = Math.max(0, dstEl.scrollHeight - dstEl.clientHeight);
          const pairs = [];
          const list = anchorsRef.current || [];
          for (let i = 0; i < list.length; i++) {
            const a = list[i];
            const lNode = leftItemRefs.current[a.idx];
            const rNode = a.rightEl;
            if (!lNode || !rNode) continue;
            const lTop = Math.max(0, relativeTop(lNode, leftEl) - 18);
            const rTop = Math.max(0, relativeTop(rNode, rightEl) - 18);
            pairs.push(driver === 'left' ? { src: lTop, dst: rTop } : { src: rTop, dst: lTop });
          }
          const targetTop = interpolateSyncedScroll(srcEl.scrollTop, pairs, srcMax, dstMax);
          syncLockRef.current = { driver, until: Date.now() + 150 };
          dstEl.scrollTop = targetTop;
        });
      };

      const jumpRightToItem = (idx) => {
        const rightEl = rightScrollRef.current;
        if (!rightEl || docxStatus !== 'ready') return;
        const found = (anchorsRef.current || []).find((a) => a.idx === idx);
        if (!found || !found.rightEl) return;
        const targetTop = Math.max(0, relativeTop(found.rightEl, rightEl) - 24);
        syncLockRef.current = { driver: 'left', until: Date.now() + 450 };
        rightEl.scrollTo({ top: targetTop, behavior: 'smooth' });
        if (flashElRef.current) flashElRef.current.classList.remove('wf-docx-flash');
        if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
        flashElRef.current = found.rightEl;
        void found.rightEl.offsetWidth;
        found.rightEl.classList.add('wf-docx-flash');
        flashTimerRef.current = setTimeout(() => {
          if (flashElRef.current) flashElRef.current.classList.remove('wf-docx-flash');
          flashTimerRef.current = null;
        }, 1450);
      };

      const handleReloadAll = () => {
        setDocxSeq((n) => n + 1);
        if (typeof onReload === 'function') onReload();
      };

      const formatRich = (text) => {
        let s = String(text || '').replace(/\r\n?/g, '\n');
        // Strip stray unmatched bold markers like leading "**______"
        s = s.replace(/\*\*([^*]*?)(?=\n|$)/g, (m, inner) => (m.endsWith('**') ? m : inner));
        // Restore paragraph/cell leading indent wrongly normalized into ______
        s = s.replace(/(^|\n|\|\s*)_{4,}(?=[A-Z][a-z]|[“"‘'《\u4e00-\u9fff])/g, '$1\u3000\u3000');
        let html = markLine(s, false);
        // Symbol/short marker wrapped by underscores and <u>, e.g. __<u>▲</u>__
        html = html.replace(/[_\uff3f]{2,}\s*<u>([^<]{1,4})<\/u>\s*[_\uff3f]{2,}/g, '<span class="wf-blank">$1</span>');
        // Underlined underscores or blank spaces -> unified fill-blank rule (avoid double lines)
        html = html.replace(/<u>[\s\u3000\xa0]*[_\uff3f]{2,}[\s\u3000\xa0]*<\/u>/g, '<span class="wf-fill-blank"></span>');
        html = html.replace(/<u>[\s\u3000\xa0]{2,}<\/u>/g, '<span class="wf-fill-blank"></span>');
        // Standalone answer-area underline on its own line gets full response width
        html = html.replace(/(^|\n)[ \t]*[_\uff3f]{3,}[ \t]*(?=\n|$)/g, '$1<span class="wf-fill-line"></span>');
        // Render inline underscore runs (______ ) as clean Word-style baseline underline rules
        html = html.replace(/[_\uff3f]{3,}/g, '<span class="wf-fill-blank"></span>');
        return html;
      };

      const renderTextWithImages = (text, keyPrefix, style, imgStyle) => {
        const s = String(text || '').replace(/\r\n?/g, '\n');
        if (!s.trim()) return null;
        const imgRe = /\[IMG\s+([^\]]+)\]/gi;
        const nodes = [];
        let last = 0;
        let m;
        let seq = 0;
        while ((m = imgRe.exec(s)) !== null) {
          if (m.index > last) {
            const chunk = s.slice(last, m.index);
            if (chunk.trim()) {
              nodes.push(h('span', { key: keyPrefix + ':s' + (seq++), dangerouslySetInnerHTML: { __html: formatRich(chunk) } }));
            }
          }
          const fname = String(m[1] || '').trim().split('/').pop();
          const url = '/api/batches/' + encodeURIComponent(batchName) + '/media/' + encodeURIComponent(fname);
          nodes.push(h('span', { key: keyPrefix + ':i' + (seq++), style: { display: imgStyle && imgStyle.display === 'block' ? 'block' : 'inline-block', verticalAlign: 'middle', margin: '4px 6px' } },
            h(PreviewImage, { url, name: fname, style: imgStyle })));
          last = imgRe.lastIndex;
        }
        if (last < s.length) {
          const tail = s.slice(last);
          if (tail.trim()) {
            nodes.push(h('span', { key: keyPrefix + ':s' + (seq++), dangerouslySetInnerHTML: { __html: formatRich(tail) } }));
          }
        }
        if (!nodes.length) return null;
        return h('div', { key: keyPrefix, style: style || { fontSize: 13.5, lineHeight: 1.8, whiteSpace: 'pre-wrap' } }, nodes);
      };

      const isImageAtt = (a) => {
        if (!a || !(a.url || a.name)) return false;
        const kind = String(a.kind || '').toLowerCase();
        if (kind === 'audio' || kind === 'video') return false;
        const target = String(a.name || a.url || '').toLowerCase().split('?')[0];
        if (/\.(mp3|wav|m4a|ogg|aac|flac|mp4|webm)$/i.test(target)) return false;
        return kind === 'image' || /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(target) || !kind;
      };

      const attPreviewUrl = (a) => {
        const raw = String((a && a.url) || '').trim();
        if (/^(?:\/api\/|\/wf-plugin\/|https?:|blob:|data:image\/)/i.test(raw)) return raw;
        const fname = String((a && (a.name || a.url)) || '').split('/').pop();
        return '/api/batches/' + encodeURIComponent(batchName) + '/media/' + encodeURIComponent(fname);
      };

      const optionKeyOf = (o) => {
        if (!o) return '';
        if (typeof o === 'object') return String(o.key || o.label || '').trim().toUpperCase();
        const m = String(o).trim().match(/^([A-H])(?:[\.．、\s]|$)/i);
        return m ? m[1].toUpperCase() : '';
      };

      const inlineImgNamesOf = (blocks) => {
        const names = new Set();
        (Array.isArray(blocks) ? blocks : []).forEach((blk) => {
          (Array.isArray(blk && blk.rows) ? blk.rows : []).forEach((r) => {
            (Array.isArray(r && r.cells) ? r.cells : []).forEach((c) => {
              const txt = String((c && c.text) || '');
              const re = /\[IMG\s+([^\]]+)\]/gi;
              let m;
              while ((m = re.exec(txt)) !== null) {
                names.add(String(m[1] || '').trim().split('/').pop());
              }
            });
          });
        });
        return names;
      };

      const renderAttachments = (attachments, keyPrefix, allowAnswer, options, materialBlocks) => {
        const list = Array.isArray(attachments) ? attachments.filter(isImageAtt) : [];
        const optKeys = new Set((Array.isArray(options) ? options : []).map(optionKeyOf).filter(Boolean));
        const inlineNames = inlineImgNamesOf(materialBlocks);
        const visible = list.filter((a) => {
          const p = String(a.placement || 'question').toLowerCase();
          if (p === 'answer' || p === 'explanation') return !!allowAnswer;
          if (p === 'option' && a.optionKey && optKeys.has(String(a.optionKey).trim().toUpperCase())) return false;
          const fname = String(a.name || a.url || '').split('/').pop();
          if (fname && inlineNames.has(fname)) return false;
          return true;
        });
        if (!visible.length) return null;
        return h('div', { key: keyPrefix, style: { display: 'flex', flexWrap: 'wrap', gap: 10, margin: '6px 0 8px' } },
          visible.map((a, idx) => {
            const fname = String(a.name || a.url || '').split('/').pop();
            const url = attPreviewUrl(a);
            return h('div', { key: keyPrefix + ':' + idx, style: { maxWidth: 260 } },
              h(PreviewImage, { url, name: fname, style: { maxWidth: 240, maxHeight: 180, objectFit: 'contain' } }));
          }),
        );
      };

      const renderTableElement = (blk, key) => {
        const rows = Array.isArray(blk && blk.rows) ? blk.rows : [];
        if (!rows.length) return null;
        return h('table', {
          key,
          style: { width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed', margin: '8px 0', fontSize: 13, background: 'var(--dsw-alias-bg-layer-1)' },
        },
          h('tbody', null, rows.map((r, ri) => h('tr', { key: ri },
            (r.cells || []).map((c, ci) => h(c.header ? 'th' : 'td', {
              key: ci,
              rowSpan: c.rowspan > 1 ? c.rowspan : undefined,
              colSpan: c.colspan > 1 ? c.colspan : undefined,
              style: { border: '1px solid var(--dsw-alias-border-l2)', padding: '6px 9px', textAlign: 'left', verticalAlign: 'top', wordBreak: 'break-word', whiteSpace: 'pre-wrap', fontWeight: c.header ? 600 : 400 },
            }, renderTextWithImages(c.text || '', key + ':c:' + ri + ':' + ci, { fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap' }, { maxWidth: 160, maxHeight: 120, objectFit: 'contain', display: 'block', margin: '4px 0' }))),
          ))),
        );
      };

      const parseFallbackTable = (inner) => {
        const lines = String(inner || '').replace(/<br\s*\/?>/gi, '\n').split('\n').map((l) => l.trim()).filter(Boolean);
        if (!lines.length) return null;
        const hasPipes = lines.some((l) => l.includes('|'));
        const rows = hasPipes
          ? lines.map((l) => ({ cells: l.split('|').map((cell) => ({ text: cell.trim(), rowspan: 1, colspan: 1 })) }))
          : [{ cells: [{ text: lines.join('\n'), rowspan: 1, colspan: 1 }] }];
        return { rows };
      };

      const renderRichBlocks = (text, materialBlocks, usedBlocks, keyPrefix) => {
        const raw = String(text || '').replace(/\r\n?/g, '\n');
        if (!raw.trim()) return null;
        const re = /\[TABLE(?:\s+(\d+))?\]([\s\S]*?)\[\/TABLE\]/gi;
        const parts = [];
        let lastIdx = 0;
        let match;
        let seq = 0;
        while ((match = re.exec(raw)) !== null) {
          if (match.index > lastIdx) {
            const seg = raw.slice(lastIdx, match.index).replace(/^\n+|\n+$/g, '');
            if (seg.trim()) {
              parts.push(renderTextWithImages(seg, keyPrefix + ':t:' + (seq++), { fontSize: 13.5, lineHeight: 1.8, whiteSpace: 'pre-wrap', marginBottom: 4 }));
            }
          }
          const tblNum = match[1] ? Number(match[1]) - 1 : -1;
          let matchedIdx = (tblNum >= 0 && Array.isArray(materialBlocks) && materialBlocks[tblNum]) ? tblNum : -1;
          if (matchedIdx < 0 && Array.isArray(materialBlocks)) {
            matchedIdx = materialBlocks.findIndex((b, idx) => b && Array.isArray(b.rows) && (!usedBlocks || !usedBlocks.has(idx)));
          }
          let blk = matchedIdx >= 0 ? materialBlocks[matchedIdx] : null;
          if (blk && usedBlocks) usedBlocks.add(matchedIdx);
          if (!blk || !Array.isArray(blk.rows) || !blk.rows.some((r) => (r.cells || []).some((c) => String(c.text || '').trim()))) {
            const parsed = parseFallbackTable(match[2]);
            if (parsed) blk = parsed;
          }
          if (blk) parts.push(renderTableElement(blk, keyPrefix + ':tbl:' + (seq++)));
          lastIdx = re.lastIndex;
        }
        if (lastIdx < raw.length) {
          const tail = raw.slice(lastIdx).replace(/^\n+|\n+$/g, '');
          if (tail.trim()) {
            parts.push(renderTextWithImages(tail, keyPrefix + ':t:' + (seq++), { fontSize: 13.5, lineHeight: 1.8, whiteSpace: 'pre-wrap' }));
          }
        }
        return parts.length ? parts : null;
      };

      const renderOptionsGrid = (options, materialBlocks, usedBlocks, keyPrefix, attachments) => {
        const rawList = Array.isArray(options) ? options : [];
        const list = [];
        const trailingBlocks = [];
        rawList.forEach((o, idx) => {
          if (!o) return;
          if (typeof o === 'string') {
            let s = o.trim();
            const splitIdx = s.search(/\n(?=(?:任务[一二三四五六七八九十\d]|\[TABLE))/);
            if (splitIdx >= 0) {
              trailingBlocks.push(s.slice(splitIdx + 1).trim());
              s = s.slice(0, splitIdx).trim();
            }
            if (s) list.push({ key: '', text: s, optKey: optionKeyOf(s) });
          } else if (typeof o === 'object' && (o.key || o.text)) {
            const k = String(o.key || '');
            list.push({ key: k, text: String(o.text || ''), optKey: optionKeyOf(o) });
          }
        });
        if (!list.length && !trailingBlocks.length) return null;
        const optImgs = {};
        (Array.isArray(attachments) ? attachments : []).forEach((a) => {
          if (!isImageAtt(a)) return;
          if (String(a.placement || '').toLowerCase() === 'option' && a.optionKey) {
            const k = String(a.optionKey).trim().toUpperCase();
            if (!optImgs[k]) optImgs[k] = [];
            optImgs[k].push(a);
          }
        });
        const hasOptImgs = list.some((o) => o.optKey && optImgs[o.optKey]);
        const maxLen = list.reduce((m, o) => Math.max(m, o.text.length + (o.key ? o.key.length + 2 : 0)), 0);
        const cols = hasOptImgs ? (list.length <= 4 ? Math.max(1, list.length) : 2)
          : (maxLen <= 16 && list.length === 4 ? 4 : (maxLen <= 34 ? 2 : 1));
        return h(React.Fragment, null,
          list.length ? h('div', {
            style: {
              display: 'grid',
              gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
              gap: hasOptImgs ? '8px 14px' : '4px 16px',
              marginTop: 4,
              paddingLeft: 18,
              alignItems: 'center',
            },
          }, list.map((o, idx) => {
            const imgs = (o.optKey && optImgs[o.optKey]) || [];
            const labelHtml = formatRich((o.key ? o.key + '. ' : '') + o.text);
            if (!imgs.length) {
              return h('div', {
                key: String(o.key || idx),
                style: { fontSize: 13.5, lineHeight: 1.7, overflowWrap: 'anywhere' },
                dangerouslySetInnerHTML: { __html: labelHtml },
              });
            }
            return h('div', {
              key: String(o.key || idx),
              style: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 13.5, lineHeight: 1.7 },
            },
              h('span', { style: { flexShrink: 0 }, dangerouslySetInnerHTML: { __html: labelHtml } }),
              imgs.map((a, ai) => h(PreviewImage, {
                key: 'oimg:' + ai,
                url: attPreviewUrl(a),
                name: String(a.name || a.url || '').split('/').pop(),
                style: { maxWidth: 110, maxHeight: 76, objectFit: 'contain', borderRadius: 4, flexShrink: 0 },
              })),
            );
          })) : null,
          trailingBlocks.map((tb, ti) => h('div', { key: 'tb:' + ti, style: { marginTop: 8 } },
            renderRichBlocks(tb, materialBlocks, usedBlocks, keyPrefix + ':tb:' + ti))),
        );
      };

      const renderMaterialBlocks = (blocks, usedBlocks) => {
        if (!Array.isArray(blocks) || !blocks.length) return null;
        return blocks.map((blk, bi) => {
          if (usedBlocks && usedBlocks.has(bi)) return null;
          return renderTableElement(blk, 'blk:' + bi);
        });
      };

      const renderItems = (items) => {
        const cleanHeading = (s) => String(s || '').replace(/\[IMG\s+[^\]]+\]/gi, '').replace(/\*{2,}/g, '').replace(/\s+/g, ' ').trim();
        const cleanBandTitle = (s) => {
          let out = cleanHeading(s);
          while (out.length >= 2 && (
            (out[0] === '【' && out[out.length - 1] === '】') ||
            (out[0] === '[' && out[out.length - 1] === ']') ||
            (out[0] === '(' && out[out.length - 1] === ')') ||
            (out[0] === '（' && out[out.length - 1] === '）')
          )) {
            out = out.slice(1, -1).trim();
          }
          return out;
        };
        const normText = (s) => cleanHeading(s).replace(/[\s，,。．.；;：:（）()【】\[\]《》“”"'\-—_]+/g, '').toLowerCase();
        const normEq = (a, b) => {
          const na = normText(a);
          const nb = normText(b);
          return Boolean(na && nb && na === nb);
        };
        const joinNumberedStem = (num, stem, allowPrefix) => {
          const s = String(stem || '').trim();
          const nStr = (num !== null && num !== undefined) ? String(num).trim() : '';
          if (!allowPrefix || !nStr) return s;
          if (!s) return nStr + '.';
          const escaped = nStr.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
          if (new RegExp('^' + escaped + '(?:[\\.．、\\)）]\\s*|$)').test(s)) return s;
          return nStr + '. ' + s;
        };
        let lastPart = '';
        let lastBand = '';
        let lastSection = '';
        let lastQType = '';
        let lastGroupStem = '';
        const profiles = [];
        const rendered = (items || []).map((it, idx) => {
          if (!it || typeof it !== 'object') {
            profiles.push({ phrases: [], numbers: [] });
            return null;
          }
          const secObj = (it.source_section && typeof it.source_section === 'object') ? it.source_section : {};
          const partTitle = cleanHeading(secObj.part || '');
          const bandTitle = cleanBandTitle(secObj.band || (secObj.lesson_band && secObj.lesson_band.label) || '');
          const secTitle = cleanHeading(secObj.title || '');
          const secInst = cleanHeading(secObj.instruction || '');
          const qType = cleanHeading(it.question_type || '');
          const partChanged = Boolean(partTitle && partTitle !== lastPart && partTitle.length <= 80);
          if (partChanged) { lastPart = partTitle; lastBand = ''; lastSection = ''; lastGroupStem = ''; }
          const bandChanged = Boolean(bandTitle && bandTitle !== lastBand && bandTitle.length <= 80);
          if (bandChanged) { lastBand = bandTitle; lastSection = ''; lastGroupStem = ''; }
          const secChanged = Boolean(secTitle && secTitle !== lastSection && secTitle.length <= 80);
          if (secChanged) { lastSection = secTitle; lastGroupStem = ''; }
          const qTypeChanged = Boolean(!secTitle && qType && lastQType && qType !== lastQType);
          if (qTypeChanged) lastGroupStem = '';
          if (qType) lastQType = qType;
          const topGap = idx > 0 ? ((partChanged || bandChanged) ? 28 : (secChanged ? 24 : (qTypeChanged ? 22 : 0))) : 0;

          const subs = Array.isArray(it.subquestions) ? it.subquestions : [];
          const isGroup = it.unit_kind === 'group' || subs.length > 0;
          const unitNum = it.original_number !== undefined && it.original_number !== null ? it.original_number : (it.number != null ? it.number : (Array.isArray(it.numbers) && it.numbers.length === 1 ? it.numbers[0] : null));
          const usedBlocks = new Set();
          const unitKey = String(it.unit_id || it.id || idx);
          const rawGroupStem = String(it.stem || '').trim();
          const normGroupStem = normText(rawGroupStem);
          const showGroupStem = Boolean(
            isGroup && rawGroupStem
            && !normEq(rawGroupStem, secTitle)
            && !normEq(rawGroupStem, secInst)
            && (!normGroupStem || normGroupStem !== lastGroupStem),
          );
          if (isGroup) {
            if (normGroupStem) lastGroupStem = normGroupStem;
          } else {
            lastGroupStem = '';
          }
          const showSecInst = Boolean(secChanged && secInst && !normEq(secInst, secTitle));
          profiles.push(buildCompareSearchProfile(it, { secChanged, secTitle, showSecInst, secInst }));

          return h('div', {
            key: unitKey,
            className: 'wf-compare-unit',
            'data-sync-idx': String(idx),
            ref: (el) => { leftItemRefs.current[idx] = el; },
            style: { marginTop: topGap, marginBottom: 14 },
          },
            docxStatus === 'ready' ? h('button', {
              type: 'button',
              className: 'wf-compare-jump',
              title: '在右侧 Word 原文件中定位到此题',
              onClick: () => jumpRightToItem(idx),
            }, '定位原文') : null,
            partChanged ? h('div', {
              style: {
                fontWeight: 700,
                fontSize: 15,
                textAlign: 'center',
                margin: '0 0 10px',
                color: 'var(--dsw-alias-label-primary)',
                letterSpacing: '0.5px',
              },
            }, partTitle) : null,
            bandChanged ? h('div', {
              style: {
                display: 'inline-block',
                fontWeight: 700,
                fontSize: 14.5,
                padding: '4px 12px',
                margin: (partChanged ? '4px' : '0') + ' 0 10px',
                borderRadius: 6,
                background: 'rgba(37, 99, 235, 0.10)',
                color: '#1d4ed8',
                borderLeft: '3px solid #2563eb',
                letterSpacing: '0.3px',
              },
            }, bandTitle) : null,
            secChanged ? h('div', {
              style: {
                fontWeight: 700,
                fontSize: 14.5,
                margin: ((partChanged || bandChanged) ? '4px' : '0') + ' 0 8px',
                color: 'var(--dsw-alias-label-primary)',
                borderBottom: '1px solid var(--dsw-alias-border-l1)',
                paddingBottom: 4,
              },
            }, secTitle) : null,
            showSecInst ? h('div', {
              style: {
                fontSize: 13,
                lineHeight: 1.75,
                color: 'var(--dsw-alias-label-secondary)',
                margin: '0 0 10px',
                textIndent: '2em',
              },
            }, secInst) : null,
            isGroup ? h('div', null,
              showGroupStem ? h('div', { style: { fontWeight: 600, marginBottom: 4 } }, renderRichBlocks(it.stem, it.materialBlocks, usedBlocks, unitKey + ':stem')) : null,
              it.material ? h('div', { style: { marginBottom: 8 } }, renderRichBlocks(it.material, it.materialBlocks, usedBlocks, unitKey + ':mat')) : null,
              renderAttachments(it.attachments, unitKey + ':att', showKey, it.options, it.materialBlocks),
              Array.isArray(it.options) && it.options.length ? h('div', { style: { marginBottom: 8, padding: '6px 10px', border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 6, background: 'var(--dsw-alias-bg-layer-2)' } },
                renderOptionsGrid(it.options, it.materialBlocks, usedBlocks, unitKey + ':gopts', it.attachments),
              ) : null,
              showKey && it.articleSummary ? h('div', { style: { fontSize: 12.5, color: 'var(--dsw-alias-label-secondary)', marginBottom: 6, padding: '4px 8px', background: 'var(--dsw-alias-bg-layer-2)', borderRadius: 6 } }, '【文章大意】' + it.articleSummary) : null,
              subs.map((sub, si) => {
                const sNum = sub.original_number !== undefined && sub.original_number !== null ? sub.original_number : (sub.number != null ? sub.number : (si + 1));
                const sStem = String(sub.stem || '').trim();
                const hasOpts = Array.isArray(sub.options) && sub.options.length > 0;
                const blankOnly = !sStem || /^[\s_]+$/.test(sStem);
                const subAtts = renderAttachments(sub.attachments, unitKey + ':s' + si + ':att', showKey, sub.options);
                const hasMaterialBlanks = Boolean(it.material && /(?:_{2,}|__\d+__|[（(]\s*\d+\s*[）)]|<u>\s*\d+\s*<\/u>)/.test(String(it.material)));
                if (blankOnly && !hasOpts && !subAtts && hasMaterialBlanks) {
                  return showKey && (sub.answer || sub.explanation)
                    ? h('div', { key: String(sNum) + ':' + si, style: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)', paddingLeft: 18, marginBottom: 4 } },
                        String(sNum) + '. ' + (sub.answer ? '答案：' + sub.answer + '　' : '') + (sub.explanation ? '解析：' + sub.explanation : ''))
                    : null;
                }
                return h('div', { key: String(sNum) + ':' + si, style: { marginBottom: 8 } },
                  blankOnly && hasOpts
                    ? h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 6, flexWrap: 'wrap', fontSize: 13.5, lineHeight: 1.75 } },
                        h('span', { style: { fontWeight: 600, flexShrink: 0 } }, String(sNum) + '.'),
                        h('div', { style: { flex: 1, minWidth: 0 } }, renderOptionsGrid(sub.options, it.materialBlocks, usedBlocks, unitKey + ':s' + si, sub.attachments)),
                      )
                    : h('div', null,
                        (!blankOnly || !hasMaterialBlanks) ? renderRichBlocks(joinNumberedStem(sNum, sStem || '______', true), it.materialBlocks, usedBlocks, unitKey + ':s' + si + ':stem') : null,
                        subAtts,
                        renderOptionsGrid(sub.options, it.materialBlocks, usedBlocks, unitKey + ':s' + si + ':opts', sub.attachments),
                      ),
                  showKey && (sub.answer || sub.explanation)
                    ? h('div', { style: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)', paddingLeft: 18, marginTop: 2 } },
                        (sub.answer ? '答案：' + sub.answer + '　' : '') + (sub.explanation ? '解析：' + sub.explanation : ''))
                    : null,
                );
              }),
              renderMaterialBlocks(it.materialBlocks, usedBlocks),
            ) : h('div', null,
              renderRichBlocks(joinNumberedStem(unitNum, it.stem, it.printed_number !== false), it.materialBlocks, usedBlocks, unitKey + ':stem'),
              renderAttachments(it.attachments, unitKey + ':att', showKey, it.options, it.materialBlocks),
              renderOptionsGrid(it.options, it.materialBlocks, usedBlocks, unitKey + ':opts', it.options ? it.attachments : null),
              renderMaterialBlocks(it.materialBlocks, usedBlocks),
              showKey && (it.answer || it.explanation)
                ? h('div', { style: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)', paddingLeft: 18, marginTop: 2 } },
                    (it.answer ? '答案：' + it.answer + '　' : '') + (it.explanation ? '解析：' + it.explanation : ''))
                : null,
            ),
          );
        });
        itemProfilesRef.current = profiles;
        return rendered;
      };

      const tags = [];
      if (data && data.source_type && data.source_type !== '未填写') tags.push({ k: '来源', v: data.source_type });
      if (data && data.year && data.year !== '未填写') tags.push({ k: '年份', v: data.year });
      if (data && Array.isArray(data.categories) && data.categories.length) tags.push({ k: '分类', v: data.categories.join(' / ') });
      const renderedItems = (!loading && !error && data && Array.isArray(data.items) && data.items.length)
        ? renderItems(data.items)
        : null;
      if (!renderedItems) itemProfilesRef.current = [];

      return h(ModalShell, {
        title: '预览对照',
        tag: data && Array.isArray(data.items) ? ('共 ' + data.items.length + ' 组/题') : '',
        width: 'min(96vw, 1560px)',
        cardStyle: { height: '90vh' },
        bodyStyle: { padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column', minHeight: 0 },
        onClose,
        footer: h('div', { style: { display: 'flex', alignItems: 'center', width: '100%', gap: 8, flexWrap: 'wrap' } },
          h('button', { type: 'button', className: 'wf-btn', style: T.btn, onClick: () => setShowKey((v) => !v) }, showKey ? '隐藏答案与解析' : '显示答案与解析'),
          h('button', {
            type: 'button',
            className: 'wf-sync-toggle',
            'aria-pressed': syncScroll ? 'true' : 'false',
            title: '开启后按题目锚点双向同步滚动左右两栏',
            onClick: () => setSyncScroll((v) => !v),
          }, syncScroll ? '同步滚动：已开启' : '同步滚动：已关闭'),
          docxStatus === 'ready' && anchorStats.total > 0
            ? h('span', { style: { ...T.hint, fontSize: 11.5 } }, '已按题对齐 ' + anchorStats.matched + ' / ' + anchorStats.total + ' 组')
            : null,
          h('span', { style: { flex: 1 } }),
          h('button', { type: 'button', className: 'wf-btn', style: T.btn, onClick: handleReloadAll, disabled: loading || docxStatus === 'loading' }, '刷新'),
          h('button', { type: 'button', className: 'wf-btn-primary', style: T.btnPrimary, onClick: onClose }, '关闭'),
        ),
      },
        h('div', { className: 'wf-compare-split' },
          // 左栏：当前已组装整卷预览
          h('div', { className: 'wf-compare-pane' },
            h('div', { className: 'wf-compare-pane-head' },
              h('span', { className: 'wf-compare-pane-title' }, '已组装试卷'),
              h('span', { className: 'wf-compare-pane-meta' }, (data && data.title) || batchName),
            ),
            h('div', {
              ref: leftScrollRef,
              className: 'wf-scroll wf-compare-scroll',
              onScroll: () => handlePaneScroll('left'),
            },
              loading ? h('div', { style: { ...T.hint, padding: '24px 0', textAlign: 'center' } }, '正在加载已组装试卷…')
                : (error ? h('div', { style: { color: 'var(--dsw-alias-state-error-primary)', padding: 12 } }, '加载失败：' + error)
                  : h('div', {
                      className: 'wf-paper',
                      style: {
                        background: 'var(--dsw-alias-bg-layer-2)',
                        border: '1px solid var(--dsw-alias-border-l1)',
                        borderRadius: 8,
                        padding: '22px 26px',
                        fontFamily: '"Times New Roman","SimSun","Songti SC",serif',
                        color: 'var(--dsw-alias-label-primary)',
                      },
                    },
                      h('div', { style: { textAlign: 'center', marginBottom: 16, paddingBottom: 12, borderBottom: '2px solid var(--dsw-alias-border-l2)' } },
                        h('div', { style: { fontSize: 17.5, fontWeight: 700, lineHeight: 1.5 } }, (data && data.title) || batchName),
                        tags.length ? h('div', { style: { display: 'flex', justifyContent: 'center', gap: 8, flexWrap: 'wrap', marginTop: 8 } },
                          tags.map((t) => h('span', {
                            key: t.k,
                            style: { fontSize: 11.5, padding: '2px 9px', borderRadius: 999, border: '1px solid var(--dsw-alias-border-l2)', background: 'var(--dsw-alias-bg-layer-1)', color: 'var(--dsw-alias-label-secondary)', fontFamily: 'sans-serif' },
                          }, t.k + '：' + t.v)),
                        ) : null,
                      ),
                      renderedItems || h('div', { style: { ...T.hint, textAlign: 'center', padding: 20 } }, '暂无已组装题目内容'),
                    )),
            ),
          ),
          // 右栏：Word 原文件（docx-preview 原排版对照）
          h('div', { className: 'wf-compare-pane' },
            h('div', { className: 'wf-compare-pane-head' },
              h('span', { className: 'wf-compare-pane-title' }, 'Word 原文件对照'),
              docxMeta && docxMeta.filename
                ? h('span', { className: 'wf-compare-pane-meta', title: docxMeta.filename }, docxMeta.filename)
                : null,
            ),
            h('div', {
              ref: rightScrollRef,
              className: 'wf-scroll wf-compare-scroll',
              onScroll: () => handlePaneScroll('right'),
            },
              docxStatus === 'loading'
                ? h('div', { style: { ...T.hint, padding: '24px 0', textAlign: 'center' } }, '正在渲染 Word 原文件…')
                : null,
              docxStatus === 'missing'
                ? h('div', { style: { ...T.hint, padding: '24px 12px', textAlign: 'center' } }, docxError || '该批次未附带 Word（.docx）原文件')
                : null,
              docxStatus === 'error'
                ? h('div', { style: { color: 'var(--dsw-alias-state-error-primary)', padding: 12 } }, 'Word 原文件渲染失败：' + docxError)
                : null,
              h('div', { ref: docxStyleRef, 'aria-hidden': 'true' }),
              h('div', {
                ref: docxBodyRef,
                className: 'wf-docx-host',
                style: { display: docxStatus === 'ready' ? 'block' : 'none' },
              }),
            ),
          ),
        ),
      );
    }

