// ============ CONFIGURAÇÃO ============
// Troque pela URL da sua API no Render
const API_URL = "https://SEU-APP.onrender.com";

// true = usa dados de exemplo (para testar sem backend)
const MODO_DEMO = true;

// ============ ELEMENTOS ============
const el = {
  status: document.getElementById("status-conexao"),
  entradas: document.getElementById("total-entradas"),
  saidas: document.getElementById("total-saidas"),
  dentro: document.getElementById("total-dentro"),
  tabela: document.getElementById("tabela-registros"),
  busca: document.getElementById("busca"),
  filtroTipo: document.getElementById("filtro-tipo"),
  btnIniciar: document.getElementById("btn-iniciar"),
  btnParar: document.getElementById("btn-parar"),
  btnManual: document.getElementById("btn-manual"),
  inputManual: document.getElementById("input-manual"),
  resultado: document.getElementById("resultado"),
};

let registros = [];
let leitor = null;
let ultimoCodigo = "";
let ultimoTempo = 0;

// ============ API ============
async function buscarRegistros() {
  if (MODO_DEMO) return registrosDemo();
  const resp = await fetch(`${API_URL}/registros`);
  if (!resp.ok) throw new Error("Erro ao buscar registros");
  return resp.json();
}

async function enviarLeitura(codigo, tipo) {
  if (MODO_DEMO) return leituraDemo(codigo, tipo);
  const resp = await fetch(`${API_URL}/acessos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ codigo, tipo }),
  });
  if (!resp.ok) {
    const erro = await resp.json().catch(() => ({}));
    throw new Error(erro.detail || "QR Code inválido");
  }
  return resp.json(); // esperado: { placa, morador, tipo, data_hora }
}

// ============ DADOS DE DEMONSTRAÇÃO ============
function registrosDemo() {
  return [
    { placa: "ABC-1D23", morador: "Maria Souza", tipo: "entrada", data_hora: new Date(Date.now() - 3600e3).toISOString() },
    { placa: "XYZ-9K87", morador: "João Lima", tipo: "saida", data_hora: new Date(Date.now() - 1800e3).toISOString() },
    { placa: "QWE-4R56", morador: "Ana Costa", tipo: "entrada", data_hora: new Date(Date.now() - 600e3).toISOString() },
  ];
}

function leituraDemo(codigo, tipo) {
  return {
    placa: "DEM-0" + Math.floor(Math.random() * 9) + "A12",
    morador: "Morador " + codigo.slice(0, 4),
    tipo,
    data_hora: new Date().toISOString(),
  };
}

// ============ INTERFACE ============
function formatarHora(iso) {
  return new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

function ehHoje(iso) {
  return new Date(iso).toDateString() === new Date().toDateString();
}

function atualizarCards() {
  const hoje = registros.filter(r => ehHoje(r.data_hora));
  const entradas = hoje.filter(r => r.tipo === "entrada").length;
  const saidas = hoje.filter(r => r.tipo === "saida").length;
  el.entradas.textContent = entradas;
  el.saidas.textContent = saidas;
  el.dentro.textContent = Math.max(entradas - saidas, 0);
}

function renderizarTabela() {
  const termo = el.busca.value.toLowerCase();
  const tipo = el.filtroTipo.value;

  const lista = registros
    .filter(r => !tipo || r.tipo === tipo)
    .filter(r => r.placa.toLowerCase().includes(termo) || r.morador.toLowerCase().includes(termo))
    .sort((a, b) => new Date(b.data_hora) - new Date(a.data_hora));

  if (lista.length === 0) {
    el.tabela.innerHTML = `<tr><td colspan="4" class="vazio">Nenhum registro encontrado</td></tr>`;
    return;
  }

  el.tabela.innerHTML = lista.map(r => `
    <tr>
      <td>${formatarHora(r.data_hora)}</td>
      <td>${r.placa}</td>
      <td>${r.morador}</td>
      <td><span class="badge ${r.tipo}">${r.tipo === "entrada" ? "Entrada" : "Saída"}</span></td>
    </tr>
  `).join("");
}

function mostrarResultado(mensagem, ok = true) {
  el.resultado.className = `resultado ${ok ? "ok" : "erro"}`;
  el.resultado.innerHTML = mensagem;
  setTimeout(() => el.resultado.classList.add("oculto"), 5000);
}

function definirStatus(online) {
  el.status.textContent = online ? "● Conectado" : "● Sem conexão";
  el.status.className = `status ${online ? "online" : "offline"}`;
}

// ============ REGISTRAR LEITURA ============
function tipoSelecionado() {
  return document.querySelector('input[name="tipo"]:checked').value;
}

async function processarCodigo(codigo) {
  codigo = codigo.trim();
  if (!codigo) return;

  // evita ler o mesmo QR várias vezes seguidas
  const agora = Date.now();
  if (codigo === ultimoCodigo && agora - ultimoTempo < 4000) return;
  ultimoCodigo = codigo;
  ultimoTempo = agora;

  try {
    const acesso = await enviarLeitura(codigo, tipoSelecionado());
    registros.push(acesso);
    atualizarCards();
    renderizarTabela();
    mostrarResultado(
      `✅ <strong>${acesso.tipo === "entrada" ? "Entrada" : "Saída"} registrada</strong><br>
       ${acesso.morador} — ${acesso.placa}`
    );
  } catch (erro) {
    mostrarResultado(`❌ ${erro.message}`, false);
  }
}

// ============ CÂMERA ============
async function iniciarCamera() {
  leitor = new Html5Qrcode("leitor");
  try {
    await leitor.start(
      { facingMode: "environment" },
      { fps: 10, qrbox: { width: 220, height: 220 } },
      texto => processarCodigo(texto)
    );
    el.btnIniciar.disabled = true;
    el.btnParar.disabled = false;
  } catch (e) {
    mostrarResultado("❌ Não foi possível acessar a câmera.", false);
  }
}

async function pararCamera() {
  if (leitor) {
    await leitor.stop();
    leitor.clear();
    leitor = null;
  }
  el.btnIniciar.disabled = false;
  el.btnParar.disabled = true;
}

// ============ EVENTOS ============
el.btnIniciar.addEventListener("click", iniciarCamera);
el.btnParar.addEventListener("click", pararCamera);
el.btnManual.addEventListener("click", () => {
  processarCodigo(el.inputManual.value);
  el.inputManual.value = "";
});
el.inputManual.addEventListener("keydown", e => {
  if (e.key === "Enter") el.btnManual.click();
});
el.busca.addEventListener("input", renderizarTabela);
el.filtroTipo.addEventListener("change", renderizarTabela);

// ============ INICIALIZAÇÃO ============
async function iniciar() {
  try {
    registros = await buscarRegistros();
    definirStatus(true);
  } catch (e) {
    definirStatus(false);
    registros = [];
  }
  atualizarCards();
  renderizarTabela();

  // atualiza a cada 10 segundos (se não estiver em modo demo)
  if (!MODO_DEMO) {
    setInterval(async () => {
      try {
        registros = await buscarRegistros();
        definirStatus(true);
        atualizarCards();
        renderizarTabela();
      } catch { definirStatus(false); }
    }, 10000);
  }
}

iniciar();