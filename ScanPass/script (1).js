// ============ CONFIGURAÇÃO ============
// Troque pela URL da sua API no Render
const API_URL = "http://127.0.0.1:5000";

// Mesma chave definida em SCANPASS_API_KEY no servidor ("" se o servidor não usar chave).
// Atenção: ela fica visível para quem abrir o app; serve apenas como barreira básica.
const API_KEY = "";

const TEMPO_LIMITE_MS = 8000;   // desiste se o servidor não responder
const LIMITE_REGISTROS = 200;   // acessos recentes carregados (200 é o máximo que a API aceita)

// true = usa dados de exemplo (para testar sem backend)
const MODO_DEMO = false;

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
  btnConsultar: document.getElementById("btn-consultar"), // opcional (veja EVENTOS)
  resultado: document.getElementById("resultado"),

  // dashboard
  graficoHoras: document.getElementById("grafico-horas"),
  picoHora: document.getElementById("pico-hora"),
  donut: document.getElementById("donut"),
  donutTotal: document.getElementById("donut-total"),
  donutEntradas: document.getElementById("donut-entradas"),
  donutSaidas: document.getElementById("donut-saidas"),
  graficoDias: document.getElementById("grafico-dias"),
  ranking: document.getElementById("ranking"),
};

let registros = [];
let leitor = null;
let ultimoCodigo = "";
let ultimoTempo = 0;

// ============ API ============
// Todas as chamadas passam por aqui: envia a chave, limita o tempo de espera e
// transforma o {"erro": "..."} devolvido pela API em um Error com a mensagem.
async function chamarApi(caminho, opcoes = {}) {
  const controle = new AbortController();
  const cronometro = setTimeout(() => controle.abort(), TEMPO_LIMITE_MS);

  let resposta;
  try {
    resposta = await fetch(`${API_URL}${caminho}`, {
      ...opcoes,
      headers: {
        "Content-Type": "application/json",
        ...(API_KEY ? { "X-API-Key": API_KEY } : {}),
        ...(opcoes.headers || {}),
      },
      signal: controle.signal,
    });
  } catch (erro) {
    throw new Error(
      erro.name === "AbortError"
        ? "O servidor demorou demais para responder."
        : "Sem conexão com o servidor."
    );
  } finally {
    clearTimeout(cronometro);
  }

  let dados = null;
  try {
    dados = await resposta.json();
  } catch {
    // resposta sem JSON
  }
  if (!resposta.ok) {
    throw new Error((dados && dados.erro) || `Erro ${resposta.status} no servidor.`);
  }
  return dados;
}

// "entrada" / "Saída" / "SAIDA" -> "ENTRADA" / "SAIDA"
function normalizarTipo(tipo) {
  return String(tipo || "entrada")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toUpperCase();
}

// Converte um registro da API para o formato usado pela tabela e pelo dashboard:
// { id, placa, morador, tipo: "entrada" | "saida", data_hora (ISO), bloco, apartamento, status }
function acessoDoRegistro(reg) {
  const tipoBruto = String(reg.tipo_movimento || "").toLowerCase();
  return {
    id: reg.id,
    placa: reg.placa || "",
    morador: reg.morador ? reg.morador.nome : "Visitante",
    tipo: tipoBruto === "chegada" ? "entrada" : tipoBruto,
    data_hora: reg.data_hora
      ? String(reg.data_hora).replace(/(\.\d{3})\d+/, "$1")
      : new Date().toISOString(),
    bloco: reg.morador ? reg.morador.bloco : "",
    apartamento: reg.morador ? reg.morador.apartamento : "",
    status: reg.status_autorizacao,
  };
}

// Acessos que entram na tabela e nos gráficos (negados ficam só no banco)
const STATUS_NA_TABELA = ["AUTORIZADO", "VISITANTE_MANUAL"];

async function buscarRegistros() {
  if (MODO_DEMO) return registrosDemo();
  const lista = await chamarApi(`/api/acessos?limite=${LIMITE_REGISTROS}`);
  return lista
    .filter(r => STATUS_NA_TABELA.includes(r.status_autorizacao))
    .map(acessoDoRegistro);
}

// Envia o texto lido do QR Code: a API confere o hash e grava o acesso no banco.
// Resposta: { status, autorizado, mensagem, duplicado, tipo, veiculo, morador, registro }
async function enviarLeitura(codigo, tipo) {
  if (MODO_DEMO) return leituraDemo(codigo, tipo);
  return chamarApi("/api/acessos/validar", {
    method: "POST",
    body: JSON.stringify({ qr_code: codigo, tipo: normalizarTipo(tipo) }),
  });
}

// Só consulta de quem é o QR Code, SEM registrar entrada/saída.
async function consultarQrCode(codigo) {
  return chamarApi("/api/qrcode/consultar", {
    method: "POST",
    body: JSON.stringify({ qr_code: codigo.trim() }),
  });
}

// ============ DADOS DE DEMONSTRAÇÃO ============
function registrosDemo() {
  const veiculos = [
    ["ABC-1D23", "Maria Souza"], ["XYZ-9K87", "João Lima"], ["QWE-4R56", "Ana Costa"],
    ["JKL-2M34", "Carlos Reis"], ["MNB-7P81", "Paula Dias"], ["RTY-5U19", "Lucas Prado"],
  ];

  // números "aleatórios" fixos (o gráfico não muda a cada F5)
  let semente = 42;
  const aleatorio = () => (semente = (semente * 16807) % 2147483647) / 2147483647;

  const agora = new Date();
  const inicioHoje = new Date(agora).setHours(0, 0, 0, 0);
  const inicio = Math.min(inicioHoje + 6 * 3600e3, agora - 3600e3); // a partir das 06h
  const lista = [];

  for (let dia = 6; dia >= 0; dia--) {
    const quantidade = dia === 0 ? 12 : 10 + Math.floor(aleatorio() * 10);
    for (let i = 0; i < quantidade; i++) {
      const [placa, morador] = veiculos[Math.floor(aleatorio() * veiculos.length)];
      const data = new Date(agora);
      data.setDate(agora.getDate() - dia);
      if (dia === 0) {
        data.setTime(inicio + aleatorio() * (agora - inicio));
      } else {
        data.setHours(6 + Math.floor(aleatorio() * 17), Math.floor(aleatorio() * 60), 0, 0);
      }
      lista.push({ placa, morador, tipo: aleatorio() < 0.65 ? "entrada" : "saida", data_hora: data.toISOString() });
    }
  }
  return lista;
}

// Resposta de exemplo no mesmo formato de POST /api/acessos/validar
function leituraDemo(codigo, tipo) {
  const tipoApi = normalizarTipo(tipo);
  const placa = "DEM-0" + Math.floor(Math.random() * 9) + "A12";
  const morador = { id: 0, nome: "Morador " + codigo.slice(0, 4), bloco: "A", apartamento: "101" };
  return {
    status: "AUTORIZADO",
    autorizado: true,
    mensagem: "ACESSO LIBERADO",
    duplicado: false,
    tipo: tipoApi,
    veiculo: { placa },
    morador,
    registro: {
      id: Date.now(),
      data_hora: new Date().toISOString(),
      tipo_movimento: tipoApi,
      status_autorizacao: "AUTORIZADO",
      placa,
      morador,
      observacao: "",
    },
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

  atualizarDashboard();
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
      <td>${esc(r.placa)}</td>
      <td>${esc(r.morador)}</td>
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

// ============ DASHBOARD ============
function esc(texto) {
  return String(texto).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function atualizarDashboard() {
  const hoje = registros.filter(r => ehHoje(r.data_hora));
  desenharHoras(hoje);
  desenharDonut(hoje);
  desenharDias();
  desenharRanking();
}

// Acessos por hora (hoje), entradas e saídas empilhadas
function desenharHoras(hoje) {
  const entradas = Array(24).fill(0);
  const saidas = Array(24).fill(0);
  hoje.forEach(r => {
    const h = new Date(r.data_hora).getHours();
    if (r.tipo === "entrada") entradas[h]++; else saidas[h]++;
  });

  const totais = entradas.map((e, h) => e + saidas[h]);
  const maior = Math.max(...totais);
  const ALTURA = 140; // altura máxima das barras em px

  el.graficoHoras.innerHTML = totais.map((total, h) => `
    <div class="hora-col" title="${h}h: ${entradas[h]} entrada(s), ${saidas[h]} saída(s)">
      <div class="hora-barra">
        <div class="seg saida" style="height:${(saidas[h] / (maior || 1)) * ALTURA}px"></div>
        <div class="seg entrada" style="height:${(entradas[h] / (maior || 1)) * ALTURA}px"></div>
      </div>
      <span class="hora-rotulo">${h % 3 === 0 ? h + "h" : ""}</span>
    </div>`).join("");

  const pico = totais.indexOf(maior);
  el.picoHora.textContent = maior > 0 ? `Pico: ${pico}h às ${pico + 1}h` : "Sem movimento hoje";
}

// Rosca: proporção de entradas e saídas de hoje
function desenharDonut(hoje) {
  const entradas = hoje.filter(r => r.tipo === "entrada").length;
  const saidas = hoje.filter(r => r.tipo === "saida").length;
  const total = entradas + saidas;
  const pct = total ? (entradas / total) * 100 : 0;

  el.donut.style.background = total
    ? `conic-gradient(var(--entrada) 0 ${pct}%, var(--saida) ${pct}% 100%)`
    : "var(--borda)";
  el.donutTotal.textContent = total;
  el.donutEntradas.textContent = entradas;
  el.donutSaidas.textContent = saidas;
}

// Total de acessos nos últimos 7 dias
function desenharDias() {
  const dias = [];
  for (let i = 6; i >= 0; i--) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    dias.push(d);
  }
  const contagens = dias.map(d => registros.filter(r => new Date(r.data_hora).toDateString() === d.toDateString()).length);
  const maior = Math.max(...contagens, 1);
  const ALTURA = 110;

  el.graficoDias.innerHTML = dias.map((d, i) => `
    <div class="dia-col" title="${contagens[i]} acesso(s)">
      <b>${contagens[i]}</b>
      <div class="dia-barra" style="height:${(contagens[i] / maior) * ALTURA}px"></div>
      <small>${d.toLocaleDateString("pt-BR", { weekday: "short" }).replace(".", "")}<br>${d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })}</small>
    </div>`).join("");
}

// Top 5 veículos com mais passagens
function desenharRanking() {
  const contagem = {};
  registros.forEach(r => {
    contagem[r.placa] = contagem[r.placa] || { morador: r.morador, total: 0 };
    contagem[r.placa].total++;
  });
  const top = Object.entries(contagem).sort((a, b) => b[1].total - a[1].total).slice(0, 5);
  const maior = top.length ? top[0][1].total : 1;

  el.ranking.innerHTML = top.length
    ? top.map(([placa, d]) => `
        <li title="${esc(d.morador)}">
          <span>${esc(placa)}</span>
          <div class="trilho"><div class="preenchimento" style="width:${(d.total / maior) * 100}%"></div></div>
          <strong>${d.total}</strong>
        </li>`).join("")
    : `<li class="vazio">Sem dados</li>`;
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
    const r = await enviarLeitura(codigo, tipoSelecionado());
    const nomeTipo = r.tipo === "ENTRADA" ? "Entrada" : "Saída";
    const quem = r.morador && r.veiculo
      ? `${esc(r.morador.nome)} — ${esc(r.veiculo.placa)}`
      : "";

    // 1) QR Code desconhecido ou veículo inativo: o servidor informa o motivo
    if (!r.autorizado) {
      mostrarResultado(
        `❌ <strong>${esc(r.mensagem)}</strong>${quem ? `<br>${quem}` : ""}`,
        false
      );
      return;
    }

    // 2) mesma leitura dentro da janela do servidor: não grava de novo
    if (r.duplicado) {
      mostrarResultado(
        `⚠️ <strong>Leitura repetida</strong><br>
         ${nomeTipo} de ${quem} já foi registrada há instantes`
      );
      return;
    }

    // 3) acesso autorizado e gravado no banco
    registros.push(acessoDoRegistro(r.registro));
    atualizarCards();
    renderizarTabela();
    mostrarResultado(
      `✅ <strong>${nomeTipo} registrada</strong><br>
       ${quem}`
    );
  } catch (erro) {
    mostrarResultado(`❌ ${esc(erro.message)}`, false);
  }
}

// Mostra de quem é um QR Code, sem registrar entrada/saída
async function mostrarDonoDoQrCode(codigo) {
  codigo = codigo.trim();
  if (!codigo) return;
  try {
    const r = await consultarQrCode(codigo);
    if (!r.veiculo) {
      mostrarResultado("❌ <strong>QR Code não cadastrado</strong>", false);
      return;
    }
    const situacao = r.autorizado ? "ativo" : "INATIVO";
    mostrarResultado(
      `ℹ️ <strong>${esc(r.morador.nome)}</strong><br>
       Bloco ${esc(r.morador.bloco)}, Apto ${esc(r.morador.apartamento)}<br>
       ${esc(r.veiculo.modelo)} ${esc(r.veiculo.cor)} — ${esc(r.veiculo.placa)} (${situacao})`,
      r.autorizado
    );
  } catch (erro) {
    mostrarResultado(`❌ ${esc(erro.message)}`, false);
  }
}

// ============ CÂMERA ============
async function iniciarCamera() {
  leitor = new Html5Qrcode("leitor");
  try {
    await leitor.start(
      { facingMode: "environment" },
      { fps: 10, qrbox: { width: 500, height: 500 } },
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
// Botão opcional: adicione <button id="btn-consultar">Consultar dono</button> ao HTML
// para ver de quem é um QR Code (digitado no campo manual) sem registrar acesso.
if (el.btnConsultar) {
  el.btnConsultar.addEventListener("click", () => {
    mostrarDonoDoQrCode(el.inputManual.value);
    el.inputManual.value = "";
  });
}
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