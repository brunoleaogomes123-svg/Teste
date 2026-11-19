/* ===== Tuzz - controle de estoque com módulo de Acuracidade ===== */
const KEY = 'logistock_v1';
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let currentUser = null;
let resetToken = new URLSearchParams(location.hash.slice(1)).get('reset');
let chatInitialized = false;
let chatPollTimer = null;
let chatFetching = false;

async function api(url, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = {...options.headers};
  if (method !== 'GET' && method !== 'HEAD') {
    const csrfResponse = await fetch('/api/csrf-token');
    const csrfData = await csrfResponse.json();
    if (!csrfResponse.ok) throw new Error(csrfData.error || 'Não foi possível validar a solicitação.');
    headers['X-CSRFToken'] = csrfData.csrf_token;
  }
  const response = await fetch(url, {...options, method, headers});
  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('application/json') ? await response.json() : {};
  if (!response.ok) throw new Error(data.error || 'Não foi possível concluir a solicitação.');
  return data;
}

function mensagem(id, texto, tipo = '') {
  const el = $(id);
  el.textContent = texto;
  el.classList.toggle('error', tipo === 'error');
  el.classList.toggle('success', tipo === 'success');
}

function mostrarLogin(tela = 'login') {
  pararChat();
  currentUser = null;
  chatInitialized = false;
  $('#app-shell').hidden = true;
  $('#login-screen').hidden = false;
  $('#login-panel').hidden = tela !== 'login';
  $('#forgot-screen').hidden = tela !== 'forgot';
  $('#reset-screen').hidden = tela !== 'reset';
}

function pararChat() {
  if (chatPollTimer !== null) clearInterval(chatPollTimer);
  chatPollTimer = null;
}

function mostrarMensagensChat(messages, inicial) {
  const container = $('#chat-messages');
  const estavaNoFim = container.scrollHeight - container.scrollTop - container.clientHeight < 60;
  const visibleIds = new Set(messages.map(message => String(message.id)));
  const existingItems = new Map();
  container.querySelectorAll('.chat-message').forEach(item => {
    if (!visibleIds.has(item.dataset.messageId)) item.remove();
    else existingItems.set(item.dataset.messageId, item);
  });
  if (messages.length) container.querySelector('.chat-empty')?.remove();

  messages.forEach((message, index) => {
    const messageId = String(message.id);
    if (existingItems.has(messageId)) return;
    const item = document.createElement('article');
    item.className = `chat-message${String(message.user_id) === String(currentUser.id) ? ' own' : ''}`;
    item.dataset.messageId = messageId;
    const header = document.createElement('header');
    const username = document.createElement('strong');
    username.textContent = message.username;
    const timestamp = document.createElement('time');
    timestamp.dateTime = message.created_at;
    const date = new Date(message.created_at);
    timestamp.textContent = Number.isNaN(date.getTime())
      ? message.created_at
      : date.toLocaleString('pt-BR');
    header.append(username, timestamp);
    if (currentUser.is_admin || String(message.user_id) === String(currentUser.id)) {
      const deleteButton = document.createElement('button');
      deleteButton.className = 'chat-delete';
      deleteButton.type = 'button';
      deleteButton.dataset.chatDelete = messageId;
      deleteButton.setAttribute('aria-label', `Excluir mensagem de ${message.username}`);
      deleteButton.textContent = 'Excluir';
      header.append(deleteButton);
    }
    const body = document.createElement('p');
    body.textContent = message.body;
    item.append(header, body);
    container.insertBefore(item, container.children[index] || null);
  });

  if (!messages.length) {
    let empty = container.querySelector('.chat-empty');
    if (!empty) {
      empty = document.createElement('p');
      empty.className = 'chat-empty';
      container.append(empty);
    }
    empty.textContent = 'Ainda não há mensagens. Comece a conversa!';
  } else if (estavaNoFim || inicial) {
    container.scrollTop = container.scrollHeight;
  }
}

async function carregarMensagensChat() {
  if (chatFetching || !currentUser) return false;
  chatFetching = true;
  const inicial = !chatInitialized;
  try {
    const result = await api('/api/chat/messages');
    if (!inicial) {
      const visibleIds = new Set(result.messages.map(message => String(message.id)));
      $('#chat-messages').querySelectorAll('.chat-message').forEach(message => {
        if (!visibleIds.has(message.dataset.messageId)) message.remove();
      });
    }
    mostrarMensagensChat(result.messages, inicial);
    chatInitialized = true;
    $('#chat-status').textContent = '';
    $('#chat-status').classList.remove('error');
    return true;
  } catch (error) {
    $('#chat-status').textContent = `Não foi possível carregar as mensagens: ${error.message}`;
    $('#chat-status').classList.add('error');
    return false;
  } finally {
    chatFetching = false;
  }
}

function iniciarChat() {
  if (!currentUser) return;
  carregarMensagensChat();
  if (chatPollTimer === null) {
    chatPollTimer = setInterval(carregarMensagensChat, 3000);
  }
}

async function mostrarApp(usuario, animarEntrada = false) {
  currentUser = usuario;
  $('#session-user').textContent = `${usuario.username}${usuario.is_admin ? ' · ADM' : ''}`;
  $('#tab-usuarios').hidden = !usuario.is_admin;
  $('#login-screen').hidden = true;
  $('#app-shell').hidden = false;
  $('#app-shell').inert = animarEntrada;
  document.querySelectorAll('.side button[data-tab]').forEach(button => {
    button.classList.toggle('on', button.dataset.tab === 'painel');
  });
  document.querySelectorAll('.tab').forEach(tab => tab.classList.toggle('on', tab.id === 'painel'));
  render();
  if (alertaPersistencia) aviso(alertaPersistencia);
  if (animarEntrada) {
    const entrada = $('#entry-animation');
    $('#entry-user').textContent = usuario.username;
    entrada.hidden = false;
    entrada.setAttribute('aria-hidden', 'false');
    const duracao = matchMedia('(prefers-reduced-motion: reduce)').matches ? 150 : 3100;
    await new Promise(resolve => setTimeout(resolve, duracao));
    entrada.hidden = true;
    entrada.setAttribute('aria-hidden', 'true');
    $('#app-shell').inert = false;
  }
}

async function carregarUsuarios() {
  const result = await api('/api/admin/users');
  $('#t-usuarios').innerHTML = result.users.map(user => tr(
    esc(user.username),
    esc(user.email),
    user.role === 'admin' ? 'ADM' : 'Usuário',
    esc(new Date(user.created_at).toLocaleString('pt-BR'))
  )).join('') || vazio(4, 'Nenhum usuário cadastrado.');
}

async function iniciar() {
  if (resetToken) {
    mostrarLogin('reset');
    return;
  }
  try {
    const sessao = await api('/api/me');
    if (sessao.authenticated) await mostrarApp(sessao);
    else mostrarLogin();
  } catch (error) {
    mostrarLogin();
    mensagem('#login-message', `Não foi possível conectar ao servidor: ${error.message}`, 'error');
  }
}
const now = () => new Date().toLocaleString('pt-BR');
const pct = v => v == null ? '—' : v.toFixed(1).replace('.', ',') + '%';
const sign = n => (n > 0 ? '+' : '') + n;
const tr = (...c) => '<tr>' + c.map(x => '<td>' + x + '</td>').join('') + '</tr>';
const vazio = (n, t) => `<tr><td colspan="${n}" class="vazio">${t}</td></tr>`;

/* ---------- dados ---------- */
let persistenciaHabilitada = true;
let alertaPersistencia = '';
function dadosIniciais() {
  return {
    produtos: [
      {id:1, sku:'CX-001', desc:'Caixa de papelão 40x30', end:'R01/01/114', saldo:120, min:40, fisico:120},
      {id:2, sku:'FT-014', desc:'Fita adesiva 48mm', end:'R01/01/115', saldo:85, min:30, fisico:82},
      {id:3, sku:'FL-203', desc:'Filme stretch 500mm', end:'R01/01/116', saldo:24, min:30, fisico:30},
      {id:4, sku:'ET-330', desc:'Etiqueta térmica 100x150', end:'R01/01/117', saldo:300, min:100, fisico:null},
      {id:5, sku:'LV-007', desc:'Luva de segurança M', end:'R01/01/118', saldo:60, min:20, fisico:57}
    ],
    movs: []
  };
}
function bancoValido(d) {
  return d && Array.isArray(d.produtos) && Array.isArray(d.movs)
    && d.produtos.every(p => p && Number.isFinite(p.id) && typeof p.sku === 'string'
      && typeof p.desc === 'string' && typeof p.end === 'string'
      && Number.isFinite(p.saldo) && Number.isFinite(p.min)
      && (p.fisico == null || Number.isFinite(p.fisico)))
    && d.movs.every(m => m && typeof m.data === 'string' && typeof m.sku === 'string'
      && typeof m.tipo === 'string' && Number.isFinite(m.qtd) && typeof m.obs === 'string');
}
function carregar() {
  let dadosSalvos;
  try {
    dadosSalvos = localStorage.getItem(KEY);
  } catch (e) {
    persistenciaHabilitada = false;
    alertaPersistencia = 'Não foi possível acessar o armazenamento local. As alterações não serão salvas.';
    return dadosIniciais();
  }
  if (dadosSalvos === null) return dadosIniciais();

  try {
    const d = JSON.parse(dadosSalvos);
    if (bancoValido(d)) return d;
  } catch (e) {
    alertaPersistencia = 'Os dados salvos estão inválidos. Eles foram preservados, mas as alterações não serão salvas.';
    persistenciaHabilitada = false;
    return dadosIniciais();
  }

  alertaPersistencia = 'Os dados salvos têm formato inválido. Eles foram preservados, mas as alterações não serão salvas.';
  persistenciaHabilitada = false;
  return dadosIniciais();
}
let db = carregar();
function salvar() {
  if (!persistenciaHabilitada) return;
  try {
    localStorage.setItem(KEY, JSON.stringify(db));
  } catch (e) {
    persistenciaHabilitada = false;
    aviso('Não foi possível salvar os dados no armazenamento local.');
  }
}
const achar = id => db.produtos.find(p => p.id === id);

function aviso(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('on');
  clearTimeout(aviso.t); aviso.t = setTimeout(() => t.classList.remove('on'), 2600);
}

/* ---------- cálculo de acuracidade ---------- */
function acurItem(p) {
  if (p.fisico == null) return null;
  const d = Math.abs(p.fisico - p.saldo);
  if (!d) return 100;
  return p.saldo ? Math.max(0, 100 - d / p.saldo * 100) : 0;
}
function stats() {
  const c = db.produtos.filter(p => p.fisico != null);
  const div = c.filter(p => p.fisico !== p.saldo);
  const total = c.reduce((a, p) => a + p.saldo, 0);
  const erro = c.reduce((a, p) => a + Math.abs(p.fisico - p.saldo), 0);
  return {
    c, div,
    itens: c.length ? (c.length - div.length) / c.length * 100 : null,
    un: c.length ? (total ? Math.max(0, 100 - erro / total * 100) : (erro ? 0 : 100)) : null
  };
}
function statusAcur(p) {
  if (p.fisico == null) return '<span class="b n">Sem contagem</span>';
  const d = p.fisico - p.saldo;
  if (!d) return '<span class="b ok">OK</span>';
  return d > 0 ? `<span class="b sobra">Sobra ${sign(d)}</span>` : `<span class="b falta">Falta ${d}</span>`;
}

/* ---------- reparo de divergência ---------- */
function ajustar(p, motivo) {
  const d = p.fisico - p.saldo;
  if (p.fisico == null || !d) return 0;
  db.movs.unshift({data: now(), sku: p.sku, tipo: 'Ajuste', qtd: d,
    obs: `Acuracidade (${motivo}): saldo ${p.saldo} → físico ${p.fisico}`});
  p.saldo = p.fisico;
  return 1;
}

/* ---------- telas ---------- */
const movRow = m => tr(esc(m.data), esc(m.sku), esc(m.tipo),
  `<span class="${m.qtd < 0 ? 'neg' : 'pos'}">${sign(m.qtd)}</span>`, esc(m.obs || ''));

function renderPainel() {
  const baixo = db.produtos.filter(p => p.saldo <= p.min);
  $('#k-sku').textContent = db.produtos.length;
  $('#k-un').textContent = db.produtos.reduce((a, p) => a + p.saldo, 0);
  $('#k-min').textContent = baixo.length;
  $('#k-acur').textContent = pct(stats().itens);
  $('#alertas').innerHTML = baixo.length
    ? baixo.map(p => `<li><b>${esc(p.sku)}</b> ${esc(p.desc)}: saldo ${p.saldo} (mínimo ${p.min})</li>`).join('')
    : '<li style="border-color:var(--ok)">Nenhum produto abaixo do mínimo.</li>';
  $('#t-ultimas').innerHTML = db.movs.slice(0, 6).map(movRow).join('') || vazio(5, 'Sem movimentações ainda.');
}

function renderEstoque() {
  const q = $('#busca').value.trim().toLowerCase();
  const lista = db.produtos.filter(p => (p.sku + p.desc + p.end).toLowerCase().includes(q));
  $('#t-estoque').innerHTML = lista.map(p => tr(
    '<b>' + esc(p.sku) + '</b>', esc(p.desc), esc(p.end || '—'), p.saldo, p.min,
    p.saldo <= p.min ? '<span class="b low">Repor</span>' : '<span class="b ok">Normal</span>',
    `<button class="btn sec sm" data-del="${p.id}">Excluir</button>`
  )).join('') || vazio(7, 'Nenhum produto encontrado.');
}

function renderMov() {
  const sel = $('#m-prod'), atual = sel.value;
  sel.innerHTML = db.produtos.map(p => `<option value="${p.id}">${esc(p.sku)} - ${esc(p.desc)}</option>`).join('');
  sel.value = db.produtos.some(p => String(p.id) === atual) ? atual : (db.produtos.length ? String(db.produtos[0].id) : '');
  $('#t-mov').innerHTML = db.movs.slice(0, 60).map(movRow).join('') || vazio(5, 'Sem movimentações ainda.');
}

function renderAcur() {
  const s = stats();
  $('#a-itens').textContent = pct(s.itens);
  $('#a-un').textContent = pct(s.un);
  $('#a-cont').textContent = s.c.length;
  $('#a-div').textContent = s.div.length;
  $('#b-todas').disabled = !s.div.length;
  $('#t-acur').innerHTML = db.produtos.map(p => {
    const d = p.fisico == null ? null : p.fisico - p.saldo;
    return tr(
      '<b>' + esc(p.sku) + '</b>', esc(p.desc), esc(p.end || '—'), p.saldo,
      `<input type="number" min="0" step="1" data-cnt="${p.id}" value="${p.fisico ?? ''}" placeholder="—">`,
      d == null ? '—' : `<span class="${d < 0 ? 'neg' : d > 0 ? 'pos' : ''}">${sign(d)}</span>`,
      pct(acurItem(p)), statusAcur(p),
      `<button class="btn sm" data-adj="${p.id}" ${d ? '' : 'disabled'}>Ajustar</button> ` +
      `<button class="btn sec sm" data-rec="${p.id}" ${p.fisico == null ? 'disabled' : ''}>Recontar</button>`
    );
  }).join('') || vazio(9, 'Cadastre produtos na aba Estoque.');
  $('#t-ajustes').innerHTML = db.movs.filter(m => m.tipo === 'Ajuste').slice(0, 10)
    .map(m => tr(esc(m.data), esc(m.sku), `<span class="${m.qtd < 0 ? 'neg' : 'pos'}">${sign(m.qtd)}</span>`, esc(m.obs))).join('')
    || vazio(4, 'Nenhum ajuste realizado.');
}

function render() { renderPainel(); renderEstoque(); renderMov(); renderAcur(); salvar(); }

/* ---------- eventos ---------- */
$('#login-form').addEventListener('submit', e => {
  e.preventDefault();
  mensagem('#login-message', 'Verificando acesso...');
  api('/api/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      username: $('#login-user').value,
      password: $('#login-password').value
    })
  }).then(usuario => mostrarApp(usuario, true)).then(() => e.target.reset()).catch(error => {
    mensagem('#login-message', error.message, 'error');
  });
});

$('#forgot-link').addEventListener('click', () => mostrarLogin('forgot'));
document.querySelectorAll('.back-to-login').forEach(button => {
  button.addEventListener('click', () => {
    resetToken = null;
    history.replaceState(null, '', location.pathname);
    mostrarLogin();
  });
});

$('#chat-form').addEventListener('submit', async e => {
  e.preventDefault();
  const button = $('#chat-send');
  button.disabled = true;
  $('#chat-status').textContent = 'Enviando mensagem...';
  $('#chat-status').classList.remove('error');
  try {
    await api('/api/chat/messages', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({body: $('#chat-input').value})
    });
    $('#chat-input').value = '';
    if (await carregarMensagensChat()) {
      $('#chat-status').textContent = 'Mensagem enviada.';
    }
  } catch (error) {
    $('#chat-status').textContent = error.message;
    $('#chat-status').classList.add('error');
  } finally {
    button.disabled = false;
  }
});

$('#forgot-form').addEventListener('submit', e => {
  e.preventDefault();
  mensagem('#forgot-message', 'Enviando solicitação...');
  api('/api/password-reset/request', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({email: $('#forgot-email').value})
  }).then(result => {
    mensagem('#forgot-message', result.message, 'success');
  }).catch(error => {
    mensagem('#forgot-message', error.message, 'error');
  });
});

$('#reset-form').addEventListener('submit', e => {
  e.preventDefault();
  if ($('#reset-password').value !== $('#reset-confirm').value) {
    mensagem('#reset-message', 'As senhas não conferem.', 'error');
    return;
  }
  if (!resetToken) {
    mensagem('#reset-message', 'Link de recuperação inválido ou expirado.', 'error');
    return;
  }
  mensagem('#reset-message', 'Atualizando senha...');
  api('/api/password-reset/confirm', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({token: resetToken, password: $('#reset-password').value})
  }).then(result => {
    resetToken = null;
    history.replaceState(null, '', location.pathname);
    e.target.reset();
    mensagem('#reset-message', result.message, 'success');
  }).catch(error => {
    mensagem('#reset-message', error.message, 'error');
  });
});

$('#create-user-form').addEventListener('submit', e => {
  e.preventDefault();
  mensagem('#admin-message', 'Criando usuário...');
  api('/api/admin/users', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      username: $('#new-username').value,
      email: $('#new-email').value,
      password: $('#new-password').value
    })
  }).then(async result => {
    e.target.reset();
    await carregarUsuarios();
    mensagem('#admin-message', `Usuário ${result.username} criado.`, 'success');
  }).catch(error => {
    mensagem('#admin-message', error.message, 'error');
  });
});

document.addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.tab) {
    if (b.dataset.tab === 'usuarios' && !currentUser?.is_admin) return;
    document.querySelectorAll('.side button').forEach(x => x.classList.toggle('on', x === b));
    document.querySelectorAll('.tab').forEach(x => x.classList.toggle('on', x.id === b.dataset.tab));
    if (b.dataset.tab === 'chat') iniciarChat();
    else pararChat();
    if (b.dataset.tab === 'usuarios') {
      mensagem('#admin-message', 'Carregando usuários...');
      carregarUsuarios().then(() => mensagem('#admin-message', '')).catch(error => {
        mensagem('#admin-message', error.message, 'error');
      });
    }
  } else if (b.id === 'logout-button') {
    api('/api/logout', {method: 'POST'}).then(() => {
      mostrarLogin();
      $('#login-form').reset();
      mensagem('#login-message', 'Você saiu da sua conta.', 'success');
    }).catch(error => aviso(error.message));
  } else if (b.dataset.chatDelete) {
    const messageId = Number(b.dataset.chatDelete);
    if (!Number.isSafeInteger(messageId) || messageId < 1
      || !confirm('Excluir permanentemente esta mensagem?')) return;
    b.disabled = true;
    api(`/api/chat/messages/${messageId}`, {method: 'DELETE'}).then(() => {
      $(`#chat-messages [data-message-id="${messageId}"]`)?.remove();
      if (!$('#chat-messages').querySelector('.chat-message')) {
        const empty = document.createElement('p');
        empty.className = 'chat-empty';
        empty.textContent = 'Ainda não há mensagens. Comece a conversa!';
        $('#chat-messages').replaceChildren(empty);
      }
      $('#chat-status').textContent = 'Mensagem excluída.';
      $('#chat-status').classList.remove('error');
    }).catch(error => {
      $('#chat-status').textContent = error.message;
      $('#chat-status').classList.add('error');
    }).finally(() => {
      b.disabled = false;
    });
  } else if (b.dataset.del) {
    const p = achar(+b.dataset.del);
    if (p && confirm(`Excluir ${p.sku}?`)) { db.produtos = db.produtos.filter(x => x !== p); render(); aviso('Produto excluído.'); }
  } else if (b.dataset.adj) {
    const p = achar(+b.dataset.adj);
    if (p && ajustar(p, $('#motivo').value)) { render(); aviso(`Saldo de ${p.sku} corrigido.`); }
  } else if (b.dataset.rec) {
    achar(+b.dataset.rec).fisico = null; render();
  }
});

document.addEventListener('change', e => {
  const i = e.target;
  if (i.dataset.cnt) {
    const v = i.value === '' ? null : Number(i.value);
    if (v !== null && (!Number.isInteger(v) || v < 0)) {
      aviso('A contagem deve ser um número inteiro igual ou maior que zero.');
      renderAcur();
      return;
    }
    achar(+i.dataset.cnt).fisico = v;
    render();
  }
});
$('#busca').addEventListener('input', renderEstoque);

$('#f-prod').addEventListener('submit', e => {
  e.preventDefault();
  const sku = $('#p-sku').value.trim().toUpperCase();
  if (db.produtos.some(p => p.sku === sku)) return aviso('Código já cadastrado.');
  db.produtos.push({id: Date.now(), sku, desc: $('#p-desc').value.trim(), end: $('#p-end').value.trim().toUpperCase(),
    saldo: +$('#p-saldo').value, min: +$('#p-min').value, fisico: null});
  e.target.reset(); render(); aviso('Produto adicionado.');
});

$('#f-mov').addEventListener('submit', e => {
  e.preventDefault();
  const p = achar(+$('#m-prod').value); if (!p) return aviso('Cadastre um produto primeiro.');
  const q = +$('#m-qtd').value, tipo = $('#m-tipo').value;
  if (tipo === 'Saída' && q > p.saldo) return aviso(`Saldo insuficiente (${p.saldo}).`);
  p.saldo += tipo === 'Entrada' ? q : -q;
  db.movs.unshift({data: now(), sku: p.sku, tipo, qtd: tipo === 'Entrada' ? q : -q, obs: $('#m-obs').value.trim()});
  e.target.reset(); render(); aviso('Movimentação registrada.');
});

$('#b-todas').addEventListener('click', () => {
  const lista = stats().div;
  if (!lista.length || !confirm(`Ajustar ${lista.length} divergência(s) com o motivo "${$('#motivo').value}"?`)) return;
  lista.forEach(p => ajustar(p, $('#motivo').value));
  render(); aviso('Divergências corrigidas.');
});

$('#b-limpar').addEventListener('click', () => {
  if (confirm('Limpar todas as contagens físicas para iniciar uma nova?')) { db.produtos.forEach(p => p.fisico = null); render(); }
});

$('#b-csv').addEventListener('click', () => {
  const linhas = [['Código', 'Descrição', 'Endereço', 'Saldo', 'Físico', 'Divergência']]
    .concat(stats().div.map(p => [p.sku, p.desc, p.end, p.saldo, p.fisico, p.fisico - p.saldo]));
  if (linhas.length === 1) return aviso('Sem divergências para exportar.');
  const csv = '\ufeff' + linhas.map(l => l.map(c => `"${String(c).replace(/"/g, '""')}"`).join(';')).join('\n');
  const a = document.createElement('a');
  const url = URL.createObjectURL(new Blob([csv], {type: 'text/csv;charset=utf-8'}));
  a.href = url;
  a.download = 'divergencias-acuracidade.csv'; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

iniciar();
