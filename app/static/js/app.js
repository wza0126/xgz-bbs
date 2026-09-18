/* ============================================================
   行知教研吧 · 前端交互（原生 JS，无依赖）
   ============================================================ */
(function () {
  'use strict';

  var CSRF = (window.BBS && window.BBS.csrf) || '';
  var MAXMB = (window.BBS && window.BBS.maxUpload) || 50;
  var MAXIMG = (window.BBS && window.BBS.maxImage) || 5;
  var UPLOAD_URL = (window.BBS && window.BBS.urls && window.BBS.urls.upload) || '/upload';
  var uploading = 0;

  function fmtSize(n) {
    n = Number(n) || 0;
    var u = ['B', 'KB', 'MB', 'GB'], i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i === 0 ? Math.round(n) : n.toFixed(1)) + ' ' + u[i];
  }

  /* ---------------- 附件上传 ---------------- */
  function initUploader(root) {
    var input = root.querySelector('.up-input');
    var drop = root.querySelector('.drop');
    var list = root.querySelector('.up-list');
    var form = root.closest('form');
    var boardId = root.getAttribute('data-board');
    var aType = root.getAttribute('data-atype') || 'draft';
    var aId = root.getAttribute('data-aid') || '0';
    if (!input || !drop || !list) { return; }

    function addHidden(id) {
      if (!form) { return; }
      var h = document.createElement('input');
      h.type = 'hidden';
      h.name = 'attach_ids';
      h.value = id;
      h.setAttribute('data-att-hidden', id);
      form.appendChild(h);
    }

    function removeHidden(id) {
      if (!form) { return; }
      form.querySelectorAll('[data-att-hidden="' + id + '"]').forEach(function (el) { el.remove(); });
    }

    function upload(file) {
      if (file.size > MAXMB * 1024 * 1024) {
        var bad = document.createElement('div');
        bad.className = 'uprow err';
        bad.innerHTML = '<span class="fic warn">!</span><span>' + file.name +
          '</span><span class="fsize">超过 ' + MAXMB + ' MB，没上传</span>';
        list.appendChild(bad);
        return;
      }

      var row = document.createElement('div');
      row.className = 'uprow';
      row.innerHTML =
        '<span class="fic">…</span>' +
        '<span class="uname-txt"></span>' +
        '<span class="fsize"></span>' +
        '<span class="uprog"><i></i></span>';
      row.querySelector('.uname-txt').textContent = file.name;
      row.querySelector('.fsize').textContent = fmtSize(file.size);
      list.appendChild(row);

      var bar = row.querySelector('.uprog i');
      var fd = new FormData();
      fd.append('file', file);
      fd.append('board_id', boardId);
      fd.append('attachable_type', aType);
      fd.append('attachable_id', aId);

      var xhr = new XMLHttpRequest();
      uploading++;
      xhr.open('POST', UPLOAD_URL, true);
      xhr.setRequestHeader('X-CSRF-Token', CSRF);
      xhr.upload.onprogress = function (e) {
        if (e.lengthComputable) {
          bar.style.width = Math.round(e.loaded * 100 / e.total) + '%';
        }
      };
      xhr.onload = function () {
        uploading--;
        var data = null;
        try { data = JSON.parse(xhr.responseText); } catch (e) { data = null; }
        if (xhr.status === 200 && data && data.ok) {
          row.className = 'uprow';
          row.querySelector('.fic').textContent = data.badge || 'FILE';
          row.querySelector('.fic').classList.add('ok');
          row.querySelector('.fsize').textContent = data.size_text;
          var pr = row.querySelector('.uprog');
          if (pr) { pr.remove(); }
          var del = document.createElement('button');
          del.type = 'button';
          del.className = 'fmini danger';
          del.textContent = '移除';
          del.addEventListener('click', function () {
            removeHidden(data.id);
            fetch(data.delete_url, {
              method: 'POST',
              headers: { 'X-CSRF-Token': CSRF, 'X-Requested-With': 'fetch' }
            }).catch(function () {});
            row.remove();
          });
          row.appendChild(del);
          addHidden(data.id);
        } else {
          row.className = 'uprow err';
          row.querySelector('.fic').textContent = '!';
          row.querySelector('.fic').classList.add('warn');
          var pr2 = row.querySelector('.uprog');
          if (pr2) { pr2.remove(); }
          row.querySelector('.fsize').textContent = (data && data.error) || '上传失败';
        }
      };
      xhr.onerror = function () {
        uploading--;
        row.className = 'uprow err';
        row.querySelector('.fsize').textContent = '网络中断，请重试';
      };
      xhr.send(fd);
    }

    input.addEventListener('change', function () {
      Array.prototype.forEach.call(input.files, upload);
      input.value = '';
    });

    ['dragenter', 'dragover'].forEach(function (ev) {
      drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add('over'); });
    });
    ['dragleave', 'drop'].forEach(function (ev) {
      drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove('over'); });
    });
    drop.addEventListener('drop', function (e) {
      var fs = e.dataTransfer && e.dataTransfer.files;
      if (fs) { Array.prototype.forEach.call(fs, upload); }
    });
  }

  document.querySelectorAll('[data-uploader]').forEach(initUploader);

  document.addEventListener('submit', function (e) {
    if (uploading > 0 && !e.target.hasAttribute('data-allow-submit')) {
      if (!window.confirm('还有文件正在上传，现在提交会漏掉它们。要继续吗？')) {
        e.preventDefault();
      }
    }
  }, true);

  /* ---------------- 已上传附件删除 ---------------- */
  document.querySelectorAll('[data-del-att]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var id = btn.getAttribute('data-del-att');
      if (!window.confirm('删除这个附件？')) { return; }
      fetch('/f/' + id + '/delete', {
        method: 'POST',
        headers: { 'X-CSRF-Token': CSRF, 'X-Requested-With': 'fetch' }
      }).then(function () {
        var el = document.getElementById('att-' + id);
        if (el) { el.remove(); }
      }).catch(function () { window.alert('删除失败，请刷新重试'); });
    });
  });

  /* ---------------- 标签块（勾选式） ----------------
     坑：chip 是 <label> 包着 <input type="checkbox">。点了 label 之后，
     除了这里手动翻 box.checked，<label> 自身的原生激活行为还会再往上翻一次，
     两次相抵 —— 表现就是「点了完全没有反应」（真实 Chromium 复现过）。
     所以必须 preventDefault() 掐掉原生那次，让 JS 成为唯一的切换入口；
     再监听 change，保证用键盘（Tab + 空格）切换时样式也能跟上。 */
  document.querySelectorAll('.chip').forEach(function (c) {
    var box = c.querySelector('input');
    if (!box) { return; }
    function sync() { c.classList.toggle('on', box.checked); }
    c.addEventListener('click', function (e) {
      if (e.target === box) { sync(); return; }
      e.preventDefault();
      box.checked = !box.checked;
      sync();
    });
    box.addEventListener('change', sync);
    sync();
  });

  /* ---------------- 楼中楼回复 ---------------- */
  document.querySelectorAll('[data-reply-to]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var pid = btn.getAttribute('data-reply-to');
      var name = btn.getAttribute('data-reply-name') || '';
      var hidden = document.getElementById('parentId');
      var bar = document.getElementById('replyToBar');
      var nm = document.getElementById('replyToName');
      if (!hidden || !bar) { return; }
      hidden.value = pid;
      if (nm) { nm.textContent = name + ' ' + (btn.closest('.post').querySelector('.floor')
        ? btn.closest('.post').querySelector('.floor').textContent : ''); }
      bar.classList.remove('hide');
      var ta = document.querySelector('#replyForm [data-rte-area]')
            || document.querySelector('#replyForm textarea');
      if (ta) { ta.focus(); ta.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
    });
  });

  var cancel = document.getElementById('cancelReply');
  if (cancel) {
    cancel.addEventListener('click', function () {
      var hidden = document.getElementById('parentId');
      var bar = document.getElementById('replyToBar');
      if (hidden) { hidden.value = ''; }
      if (bar) { bar.classList.add('hide'); }
    });
  }

  /* ---------------- 富文本编辑器 ----------------
     页面本来渲染的是普通 <textarea>（没开 JS 也能发帖），这里把它换成所见即所得编辑区。

     提交协议：把编辑区的 innerHTML 写进 textarea，并把同级的 body_format 设成 'html'。
     服务端据此走富文本分支，并再做一次白名单清洗（app/richtext.py）。
     编辑区清空时两个字段一起复位成纯文本，免得往库里存一堆 <br>。

     这里用的是 document.execCommand —— 虽然被标了 deprecated，但主流浏览器至今完整支持；
     自己不引依赖手撸 Range 反而更容易踩兼容坑，这是当前最省的选择。 */
  function initEditor(root) {
    var bar = root.querySelector('[data-rte-bar]');
    var area = root.querySelector('[data-rte-area]');
    var src = root.querySelector('[data-rte-src]');
    var fmtIn = root.querySelector('[data-rte-format]');
    var hint = root.querySelector('[data-rte-hint]');
    var pop = root.querySelector('[data-emoji-pop]');
    var form = root.closest('form');
    if (!bar || !area || !src || !fmtIn) { return; }

    var savedRange = null;

    function isBlank() {
      return !area.textContent.trim() && !area.querySelector('img,hr');
    }

    function setPlainText(text) {
      area.textContent = '';
      String(text).split('\n').forEach(function (line, i) {
        if (i) { area.appendChild(document.createElement('br')); }
        area.appendChild(document.createTextNode(line));
      });
    }

    function sync() {
      var empty = isBlank();
      src.value = empty ? '' : area.innerHTML;
      fmtIn.value = empty ? 'text' : 'html';
      area.classList.toggle('is-empty', empty);
      syncAttachIds();
    }

    // 打字/删字都要实时同步：textarea 才是提交时真正被读的字段，
    // 漏了这个监听，占位提示会一直压在已输入的文字上（真实浏览器里复现过）。
    area.addEventListener('input', function () { sync(); refreshBar(); });
    area.addEventListener('blur', sync);

    function grabRange() {
      var sel = window.getSelection();
      if (sel && sel.rangeCount) {
        var r = sel.getRangeAt(0);
        if (area.contains(r.commonAncestorContainer)) { savedRange = r.cloneRange(); }
      }
    }

    function putRange() {
      if (!savedRange) { return false; }
      try {
        var sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(savedRange);
        return true;
      } catch (e) { return false; }     // 选区所在的节点可能已经被改动过
    }

    function caretToEnd() {
      var r = document.createRange();
      r.selectNodeContents(area);
      r.collapse(false);
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(r);
    }

    function insertAtCaret(text) {
      try {
        if (document.execCommand('insertText', false, text)) { return; }
      } catch (e) { /* 落到下面的手写兜底 */ }
      var sel = window.getSelection();
      if (!sel || !sel.rangeCount) { return; }
      var r = sel.getRangeAt(0);
      r.deleteContents();
      var frag = document.createDocumentFragment();
      String(text).split('\n').forEach(function (line, i) {
        if (i) { frag.appendChild(document.createElement('br')); }
        frag.appendChild(document.createTextNode(line));
      });
      var last = frag.lastChild;
      r.insertNode(frag);
      if (last) {
        r.setStartAfter(last);
        r.collapse(true);
        sel.removeAllRanges();
        sel.addRange(r);
      }
    }

    // ---- 初始内容 ----
    // 关键：纯文本要转义后插入，绝不能直接当 HTML 塞进去（否则老帖子一编辑就变形）
    var init = area.getAttribute('data-init') || '';
    if (init) {
      if ((area.getAttribute('data-init-fmt') || 'text') === 'html') {
        area.innerHTML = init;
      } else {
        setPlainText(init);
      }
    }
    bar.classList.remove('hide');
    area.classList.remove('hide');
    if (hint) { hint.classList.remove('hide'); }
    src.classList.add('hide');

    // 回车产出的块统一用 <p>：输出干净，服务端白名单也好洗
    try {
      document.execCommand('styleWithCSS', false, false);
      document.execCommand('defaultParagraphSeparator', false, 'p');
    } catch (e) { /* 不支持就用浏览器默认行为 */ }

    // ---- 工具栏 ----
    var cmdBtns = Array.prototype.slice.call(bar.querySelectorAll('[data-cmd]'));
    cmdBtns.forEach(function (btn) {
      // 按下时别让按钮抢走焦点，否则选区就丢了
      btn.addEventListener('mousedown', function (e) { e.preventDefault(); });
      btn.addEventListener('click', function () {
        var cmd = btn.getAttribute('data-cmd');
        var arg = btn.getAttribute('data-arg') || null;
        area.focus();
        if (!putRange()) { caretToEnd(); }
        try {
          if (cmd === 'formatBlock') {
            var cur = '';
            try { cur = String(document.queryCommandValue('formatBlock') || '').toLowerCase(); } catch (e2) {}
            // 再点一次退回普通段落，做成开关
            document.execCommand('formatBlock', false,
              cur === String(arg).toLowerCase() ? 'p' : arg);
          } else {
            document.execCommand(cmd, false, arg);
          }
        } catch (e2) { /* 命令不支持就静默跳过 */ }
        grabRange();
        refreshBar();
        sync();
      });
    });

    var linkBtn = bar.querySelector('[data-link]');
    if (linkBtn) {
      linkBtn.addEventListener('mousedown', function (e) { e.preventDefault(); });
      linkBtn.addEventListener('click', function () {
        area.focus();
        grabRange();
        var sel = window.getSelection();
        if (!sel || sel.isCollapsed || !sel.toString().trim()) {
          window.alert('先在正文里用鼠标选中要加链接的文字，再点这个按钮。');
          return;
        }
        var url = window.prompt('输入网址（例：https://www.qq.com）：', 'https://');
        if (!url) { return; }
        url = url.trim();
        if (!/^https?:\/\//i.test(url) && url.charAt(0) !== '/') {
          url = 'https://' + url.replace(/^\/+/, '');
        }
        area.focus();
        putRange();
        try { document.execCommand('createLink', false, url); } catch (e) {}
        grabRange();
        sync();
      });
    }

    // ---- 表情 ----
    var emoBtn = bar.querySelector('[data-emoji-toggle]');
    if (emoBtn && pop) {
      emoBtn.addEventListener('mousedown', function (e) { e.preventDefault(); });
      emoBtn.addEventListener('click', function () { pop.classList.toggle('hide'); });
      pop.querySelectorAll('[data-emoji]').forEach(function (b) {
        b.addEventListener('mousedown', function (e) { e.preventDefault(); });
        b.addEventListener('click', function () {
          var ch = b.getAttribute('data-emoji') || '';
          if (!ch) { return; }
          area.focus();
          if (!putRange()) { caretToEnd(); }
          insertAtCaret(ch);
          grabRange();
          sync();
        });
      });
      document.addEventListener('click', function (e) {
        if (!pop.classList.contains('hide') && !root.contains(e.target)) {
          pop.classList.add('hide');
        }
      });
    }

    // ---- 图片 ----
    // 插图走附件体系：上传成 draft 附件 → 拿到 /f/<id>/inline 插进正文 →
    // 同时把 id 记进隐藏字段 attach_ids，提交时被认领到这条话题/回复上。
    // 三个入口（工具栏按钮 / 拖进来 / 粘贴）最后都汇到 uploadImage()。
    var imgBtn = bar.querySelector('[data-img]');
    var imgInput = root.querySelector('[data-rte-img]');
    var upBar = root.querySelector('[data-rte-up]');
    var boardId = root.getAttribute('data-rte-board') || '';
    var pendingImgs = 0;
    var upTimer = null;

    function noteUp(text, isErr) {
      if (!upBar) { return; }
      upBar.textContent = text;
      upBar.classList.toggle('err', !!isErr);
      upBar.classList.remove('hide');
    }

    function clearUpLater(ms) {
      if (upTimer) { clearTimeout(upTimer); }
      upTimer = setTimeout(function () {
        if (!pendingImgs && upBar) { upBar.classList.add('hide'); }
      }, ms);
    }

    function addHiddenAtt(id) {
      if (!form) { return; }
      var has = form.querySelector('input[name="attach_ids"][value="' + id + '"]');
      if (has) { return; }
      var h = document.createElement('input');
      h.type = 'hidden';
      h.name = 'attach_ids';
      h.value = id;
      h.setAttribute('data-rte-att', id);
      form.appendChild(h);
    }

    // 正文里删掉的图，对应的隐藏字段也一起撤掉，免得提交后附件区
    // 冒出一张正文里根本没有的图。服务端只认正文里出现过的图，这里保持一致。
    function syncAttachIds() {
      if (!form) { return; }
      var used = {};
      area.querySelectorAll('img[src]').forEach(function (im) {
        var m = /\/f\/(\d+)\/inline/.exec(im.getAttribute('src') || '');
        if (m) { used[m[1]] = 1; }
      });
      form.querySelectorAll('input[data-rte-att]').forEach(function (h) {
        if (!used[h.value]) { h.remove(); }
      });
    }

    function insertImage(src, alt) {
      area.focus();
      if (!putRange()) { caretToEnd(); }
      var sel = window.getSelection();
      if (!sel || !sel.rangeCount) { return; }
      var r = sel.getRangeAt(0);
      var img = document.createElement('img');
      img.src = src;
      img.alt = alt || '';
      r.deleteContents();
      r.insertNode(img);
      // 图后面补一个空段落：不然紧接着打的字会挤在图片同一行里，很难接着编辑
      var p = document.createElement('p');
      p.appendChild(document.createElement('br'));
      if (img.nextSibling) { img.parentNode.insertBefore(p, img.nextSibling); }
      else { img.parentNode.appendChild(p); }
      var nr = document.createRange();
      nr.setStart(p, 0);
      nr.collapse(true);
      sel.removeAllRanges();
      sel.addRange(nr);
      grabRange();
      sync();
    }

    function uploadImage(file) {
      if (file.size > MAXIMG * 1024 * 1024) {
        noteUp('图片超过 ' + MAXIMG + ' MB：' + (file.name || '这张图'), true);
        return;
      }
      var fd = new FormData();
      fd.append('file', file);
      fd.append('board_id', boardId);
      fd.append('attachable_type', 'draft');
      fd.append('attachable_id', '0');
      fd.append('kind', 'image');
      var xhr = new XMLHttpRequest();
      pendingImgs++;
      uploading++;
      noteUp('图片上传中…');
      xhr.open('POST', UPLOAD_URL, true);
      xhr.setRequestHeader('X-CSRF-Token', CSRF);
      xhr.onload = function () {
        pendingImgs--;
        uploading--;
        var data = null;
        try { data = JSON.parse(xhr.responseText); } catch (e) { data = null; }
        if (xhr.status === 200 && data && data.ok && data.preview) {
          insertImage(data.preview, data.name);
          addHiddenAtt(data.id);
          syncAttachIds();
          noteUp('已插入 ' + (data.name || '图片'));
          clearUpLater(2500);
        } else {
          noteUp((data && data.error) || '图片上传失败，请重试', true);
        }
      };
      xhr.onerror = function () {
        pendingImgs--;
        uploading--;
        noteUp('网络中断，图片没传上去', true);
      };
      xhr.send(fd);
    }

    function handleFiles(files) {
      Array.prototype.forEach.call(files || [], function (f) {
        if (!/^image\//i.test(f.type || '')) {
          noteUp('只能插入图片文件（' + (f.name || '这个文件') + '）', true);
          return;
        }
        uploadImage(f);
      });
    }

    if (imgBtn && imgInput) {
      imgBtn.addEventListener('mousedown', function (e) { e.preventDefault(); });
      imgBtn.addEventListener('click', function () {
        grabRange();                 // 先记住光标，选完文件回来还要插在原来位置
        imgInput.click();
      });
      imgInput.addEventListener('change', function () {
        handleFiles(imgInput.files);
        imgInput.value = '';
      });
    }

    // ---- 粘贴 ----
    // 剪贴板里带图片（截图、复制来的图）就走上传；否则一律按纯文本收 ——
    // 从 Word / 网页粘过来的东西带着大量样式和隐藏标签，服务端反正会剥掉，
    // 与其粘完变形，不如直接给干净的文字。
    area.addEventListener('paste', function (e) {
      var dt = e.clipboardData || window.clipboardData;
      if (!dt) { return; }
      var files = [];
      if (dt.items && dt.items.length) {
        for (var i = 0; i < dt.items.length; i++) {
          var it = dt.items[i];
          if (it.kind === 'file' && /^image\//i.test(it.type || '')) {
            var f = it.getAsFile();
            if (f) { files.push(f); }
          }
        }
      }
      e.preventDefault();
      if (files.length) {
        grabRange();
        handleFiles(files);
        return;
      }
      var text = dt.getData('text/plain') || '';
      if (text) { insertAtCaret(text); }
      grabRange();
      sync();
    });

    // 把图片拖进编辑区直接上传；非图片文件挡掉（要当附件请用下面的附件区）
    ['dragover', 'drop'].forEach(function (ev) {
      area.addEventListener(ev, function (e) {
        e.preventDefault();
        if (ev === 'drop' && e.dataTransfer && e.dataTransfer.files.length) {
          grabRange();
          handleFiles(e.dataTransfer.files);
        }
      });
    });

    // ---- 工具栏高亮 ----
    var STATE_CMDS = ['bold', 'italic', 'underline', 'strikeThrough',
                      'insertUnorderedList', 'insertOrderedList'];
    function refreshBar() {
      cmdBtns.forEach(function (btn) {
        var cmd = btn.getAttribute('data-cmd');
        var on = false;
        if (STATE_CMDS.indexOf(cmd) >= 0) {
          try { on = document.queryCommandState(cmd); } catch (e) {}
        } else if (cmd === 'formatBlock' && btn.getAttribute('data-arg')) {
          var cur = '';
          try { cur = String(document.queryCommandValue('formatBlock') || '').toLowerCase(); } catch (e) {}
          on = cur === btn.getAttribute('data-arg').toLowerCase();
        }
        btn.classList.toggle('on', !!on);
      });
    }
    area.addEventListener('keyup', refreshBar);
    area.addEventListener('mouseup', refreshBar);
    document.addEventListener('selectionchange', function () {
      var sel = window.getSelection();
      if (sel && sel.rangeCount) {
        var r = sel.getRangeAt(0);
        if (area.contains(r.commonAncestorContainer)) {
          savedRange = r.cloneRange();
          refreshBar();
        }
      }
    });

    sync();
    if (form) { form.addEventListener('submit', sync); }
  }

  document.querySelectorAll('[data-rte]').forEach(initEditor);

  /* ---------------- 未读数轮询 ---------------- */
  var badge = document.getElementById('notifBadge');
  var countUrl = window.BBS && window.BBS.urls && window.BBS.urls.notifCount;
  if (badge && countUrl) {
    setInterval(function () {
      fetch(countUrl, { headers: { 'X-Requested-With': 'fetch' } })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          badge.textContent = d.unread;
          badge.classList.toggle('hide', !d.unread);
        })
        .catch(function () {});
    }, 60000);
  }
})();
