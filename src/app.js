(function () {
  var toastEl = document.getElementById('toast');
  var toastTimer;
  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('show'); }, 2200);
  }

  // 今天（美東時間）加上藍色外框
  try {
    var todayET = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date());
    var todayCol = document.querySelector('.day[data-date="' + todayET + '"]');
    if (todayCol) todayCol.classList.add('today');
  } catch (e) {}

  // 顯示更多 / 收合
  document.querySelectorAll('.more').forEach(function (btn) {
    var label = btn.querySelector('span');
    var original = label.textContent;
    btn.addEventListener('click', function () {
      var list = document.getElementById(btn.getAttribute('aria-controls'));
      var open = btn.getAttribute('aria-expanded') !== 'true';
      list.querySelectorAll('li.extra').forEach(function (li) { li.hidden = !open; });
      btn.setAttribute('aria-expanded', String(open));
      label.textContent = open ? '收合' : original;
    });
  });

  // 鍵盤 ← → 切換週次
  document.addEventListener('keydown', function (e) {
    if (e.target.closest('input, textarea, select, [contenteditable]')) return;
    var link = e.key === 'ArrowLeft' ? document.querySelector('a.circle.prev')
             : e.key === 'ArrowRight' ? document.querySelector('a.circle.next') : null;
    if (link) location.href = link.href;
  });

  // 分享
  document.getElementById('share').addEventListener('click', function () {
    var url = location.href;
    if (navigator.share) {
      navigator.share({ title: document.title, url: url }).catch(function () {});
      return;
    }
    if (navigator.clipboard) {
      navigator.clipboard.writeText(url).then(function () { toast('已複製連結'); },
        function () { toast(url); });
    } else {
      toast(url);
    }
  });

  // 下載成圖片（展開所有公司後截圖）
  document.getElementById('download').addEventListener('click', function () {
    var btn = this;
    if (!window.htmlToImage) { toast('圖片工具載入中，請稍後再試'); return; }
    var node = document.getElementById('capture');
    var hidden = Array.prototype.slice.call(node.querySelectorAll('li.extra[hidden]'));
    hidden.forEach(function (li) { li.hidden = false; });
    node.classList.add('capturing');
    btn.disabled = true;
    toast('產生圖片中…');
    var bg = getComputedStyle(document.body).backgroundColor;
    // lazy 圖片在畫面外還沒載入，先強制全部載入再截圖
    var waits = Array.prototype.map.call(node.querySelectorAll('img'), function (img) {
      img.loading = 'eager';
      if (img.complete && img.naturalWidth) return Promise.resolve();
      return new Promise(function (res) {
        img.addEventListener('load', res, { once: true });
        img.addEventListener('error', res, { once: true });
        setTimeout(res, 8000);
      });
    });
    Promise.all(waits)
      .then(function () {
        return window.htmlToImage.toPng(node, { pixelRatio: 2, backgroundColor: bg, cacheBust: false });
      })
      .then(function (dataUrl) {
        var a = document.createElement('a');
        a.href = dataUrl;
        a.download = btn.getAttribute('data-file') || 'earnings.png';
        document.body.appendChild(a);
        a.click();
        a.remove();
        toast('已下載圖片');
      })
      .catch(function () { toast('下載失敗，請再試一次'); })
      .finally(function () {
        node.classList.remove('capturing');
        hidden.forEach(function (li) {
          var btnMore = document.querySelector('[aria-controls="' + li.parentNode.id + '"]');
          if (!btnMore || btnMore.getAttribute('aria-expanded') !== 'true') li.hidden = true;
        });
        btn.disabled = false;
      });
  });
})();
