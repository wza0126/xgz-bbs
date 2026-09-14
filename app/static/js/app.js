/* ============================================================
   行知教研吧 · 前端交互（原生 JS，无依赖）
   ============================================================ */
(function () {
  'use strict';

  var CSRF = (window.BBS && window.BBS.csrf) || '';
  var MAXMB = (window.BBS && window.BBS.maxUpload) || 50;
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
      var ta = document.querySelector('#replyForm textarea');
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
