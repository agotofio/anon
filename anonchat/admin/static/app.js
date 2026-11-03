// Лайтбокс для картинок
document.addEventListener('click', (e) => {
  const img = e.target.closest('img[data-zoom]');
  const lb = document.getElementById('lightbox');
  if (img && lb) {
    lb.querySelector('img').src = img.src;
    lb.style.display = 'grid';
  } else if (e.target.closest('#lightbox')) {
    lb.style.display = 'none';
  }
  if (e.target.closest('[data-menu]')) {
    document.querySelector('.sidebar').classList.toggle('open');
  }
});

// Подтверждение опасных действий
document.addEventListener('submit', (e) => {
  const msg = e.target.dataset.confirm;
  if (msg && !confirm(msg)) e.preventDefault();
});

// Живое обновление чата
(function () {
  const chat = document.getElementById('chat');
  if (!chat || !chat.dataset.poll) return;
  const scrollDown = () => { chat.scrollTop = chat.scrollHeight; };
  scrollDown();
  const lastId = () => {
    const items = chat.querySelectorAll('[data-id]');
    return items.length ? items[items.length - 1].dataset.id : 0;
  };
  async function poll() {
    try {
      const r = await fetch(chat.dataset.poll + '?after=' + lastId(), { credentials: 'same-origin' });
      if (r.ok) {
        const html = (await r.text()).trim();
        if (html) {
          const nearBottom = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 120;
          chat.querySelector('.empty')?.remove();
          chat.insertAdjacentHTML('beforeend', html);
          if (nearBottom) scrollDown();
        }
      }
    } catch (e) { /* сеть недоступна — попробуем позже */ }
    setTimeout(poll, 4000);
  }
  setTimeout(poll, 4000);
})();

// Автообновление страниц с data-refresh
(function () {
  const el = document.querySelector('[data-refresh]');
  if (el) setTimeout(() => location.reload(), +el.dataset.refresh * 1000);
})();
