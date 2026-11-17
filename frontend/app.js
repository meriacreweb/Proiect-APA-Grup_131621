/* StegVault browser client. Secrets stay in transient page state; no web storage. */
"use strict";

(() => {
  const MAX_FILE_BYTES = 10 * 1024 * 1024;
  const MAX_PASSWORD_BYTES = 1024;
  const browserProcessing = document.body.dataset.processing === "browser";
  const encoder = new TextEncoder();
  const $ = (id) => document.getElementById(id);
  const formatNumber = (value) => new Intl.NumberFormat("ro-RO").format(value);
  const byteLength = (value) => encoder.encode(value).byteLength;
  const state = {
    embed: { file: null, preview: null, capacity: null, controller: null, capacityController: null, demoController: null, version: 0, operation: 0, busy: false },
    extract: { file: null, preview: null, controller: null, version: 0, operation: 0, busy: false },
    result: { blob: null, url: null, original: null, controller: null, version: 0, preview: "original" },
    toastTimer: null,
  };

  class UserError extends Error {
    constructor(message) { super(message); this.name = "UserError"; }
  }

  function showError(id, error) {
    const element = $(id);
    element.textContent = error instanceof Error ? error.message : String(error);
    element.hidden = false;
  }

  function hideError(id) {
    $(id).hidden = true;
    $(id).textContent = "";
  }

  function revokePreview(kind) {
    const item = state[kind];
    $(`${kind}-preview`).removeAttribute("src");
    if (item.preview) URL.revokeObjectURL(item.preview);
    item.preview = null;
    $(`${kind}-preview-wrap`).hidden = true;
    $(`${kind}-empty`).hidden = false;
    $(`${kind}-file-summary`).hidden = true;
    $(`${kind}-file-name`).textContent = "";
  }

  function clearEmbedResult() {
    state.result.version += 1;
    state.result.controller?.abort();
    state.result.controller = null;
    $("result-image").removeAttribute("src");
    $("download-image").removeAttribute("href");
    if (state.result.url) URL.revokeObjectURL(state.result.url);
    state.result.url = null;
    state.result.blob = null;
    state.result.original = null;
    setEmbedPreview("original");
    $("embed-result").hidden = true;
    $("analysis-results").hidden = true;
    $("chi-square-output").textContent = "";
    $("metric-psnr").textContent = "—";
    $("metric-ssim").textContent = "—";
    hideError("analyze-error");
    setButtonBusy($("analyze-button"), false);
  }

  function clearExtractResult() {
    $("decrypted-message").textContent = "";
    $("extracted-byte-count").textContent = "";
    $("copy-feedback").textContent = "";
    $("extract-result").hidden = true;
  }

  function resetPasswordVisibility(id) {
    $(id).type = "password";
    const button = document.querySelector(`[data-password-target="${id}"]`);
    button.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-label", id === "embed-password" ? "Arată parola de criptare" : "Arată parola de decriptare");
  }

  function setButtonBusy(button, busy, busyText) {
    const label = button.querySelector(".button-label");
    if (label && !button.dataset.idleLabel) button.dataset.idleLabel = label.textContent;
    button.disabled = busy;
    button.setAttribute("aria-busy", String(busy));
    const spinner = button.querySelector(".spinner");
    if (spinner) spinner.hidden = !busy;
    if (label) label.textContent = busy ? busyText : button.dataset.idleLabel;
  }

  function setFormBusy(kind, busy) {
    state[kind].busy = busy;
    if (kind === "embed" && busy) cancelDemo();
    const form = $(`${kind}-form`);
    form.setAttribute("aria-busy", String(busy));
    for (const control of form.querySelectorAll("input,textarea,button")) control.disabled = busy;
    $(`${kind}-dropzone`).classList.toggle("is-disabled", busy);
    setButtonBusy($(`${kind}-submit`), busy, kind === "embed" ? "Criptare în curs…" : "Decriptare în curs…");
    if (kind === "embed") {
      updateDemoButton();
      setEmbedPreview(state.result.preview);
    }
  }

  function setEmbedPreview(preview) {
    const original = $("preview-original");
    const result = $("preview-result");
    const sourcePane = $("embed-source-pane");
    const outputPane = $("embed-output-preview");
    if (!original || !result || !sourcePane || !outputPane) return;
    const showResult = preview === "result" && Boolean(state.result.blob);
    state.result.preview = showResult ? "result" : "original";
    original.setAttribute("aria-pressed", String(!showResult));
    result.setAttribute("aria-pressed", String(showResult));
    original.disabled = state.embed.busy;
    result.disabled = state.embed.busy || !state.result.blob;
    sourcePane.hidden = showResult;
    outputPane.hidden = !showResult;
  }

  function updateDemoButton() {
    const button = $("embed-demo");
    if (!button) return;
    setButtonBusy(button, Boolean(state.embed.demoController), "Se încarcă…");
    button.disabled ||= state.embed.busy;
  }

  function cancelDemo() {
    state.embed.demoController?.abort();
    state.embed.demoController = null;
    updateDemoButton();
  }

  function focusResult(kind, target) {
    if (!target || $(`panel-${kind}`).hidden) return;
    if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
    target.focus({ preventScroll: true });
    if (!window.matchMedia("(max-width: 760px)").matches) return;
    const { top, bottom } = target.getBoundingClientRect();
    if (top >= 0 && bottom <= window.innerHeight) return;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    target.scrollIntoView({ block: "center", behavior: reducedMotion ? "instant" : "smooth" });
  }

  function requestInBrowser(path, form, signal) {
    if (!window.isSecureContext || !window.crypto?.subtle || !window.Worker) {
      throw new UserError("Procesarea locală necesită un browser actual și o conexiune HTTPS.");
    }
    signal.throwIfAborted();
    return new Promise((resolve, reject) => {
      const worker = new Worker("/image-worker.js");
      const cancel = () => finish(reject, signal.reason);
      const finish = (callback, value) => {
        signal.removeEventListener("abort", cancel);
        worker.terminate();
        callback(value);
      };
      const failure = () => finish(reject, new UserError("Motorul de procesare nu a putut porni. Reîncarcă pagina."));
      signal.addEventListener("abort", cancel, { once: true });
      worker.addEventListener("error", failure, { once: true });
      worker.addEventListener("messageerror", failure, { once: true });
      worker.addEventListener("message", ({ data }) => {
        if (data.error) finish(reject, new UserError(data.error));
        else finish(resolve, data.result);
      }, { once: true });
      try {
        worker.postMessage({ path, fields: Object.fromEntries(form) });
      } catch {
        failure();
      }
    });
  }

  async function request(path, data, controller, expectImage = false) {
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 120_000);
    try {
      if (browserProcessing) return await requestInBrowser(path, data, controller.signal);
      const response = await fetch(path, {
        method: "POST", body: data, signal: controller.signal,
        cache: "no-store", credentials: "same-origin",
        headers: { Accept: expectImage ? "image/png, application/json" : "application/json" },
      });
      if (!response.ok) {
        let detail;
        try { detail = await response.json(); } catch { detail = null; }
        const message = typeof detail?.error?.message === "string" ? detail.error.message.slice(0, 700) :
          response.status === 413 ? "Fișierul depășește limita permisă de server." :
          response.status === 429 ? "Prea multe încercări. Reîncearcă în câteva momente." :
          response.status >= 500 ? "Serverul este temporar indisponibil. Încearcă din nou." :
          "Cererea nu a putut fi procesată. Încearcă din nou.";
        throw new UserError(message);
      }
      if (expectImage) {
        if (!response.headers.get("content-type")?.toLowerCase().startsWith("image/png")) throw new UserError("Serverul nu a returnat o imagine PNG validă.");
        const blob = await response.blob();
        if (!blob.size) throw new UserError("Imaginea returnată de server este goală.");
        return blob;
      }
      return await response.json();
    } catch (error) {
      if (timedOut) throw new UserError("Procesarea a durat prea mult. Încearcă din nou cu o imagine mai mică.");
      if (error.name === "AbortError" || controller.signal.aborted) throw error;
      if (error instanceof UserError) throw error;
      if (browserProcessing) throw new UserError("Procesarea în browser a eșuat. Încearcă din nou.");
      if (!navigator.onLine) throw new UserError("Ești offline. Verifică rețeaua și încearcă din nou.");
      if (error instanceof TypeError) throw new UserError("Serverul nu răspunde. Verifică dacă aplicația este pornită.");
      throw new UserError("Răspunsul serverului nu a putut fi citit. Încearcă din nou.");
    } finally {
      clearTimeout(timeout);
    }
  }

  async function validateFile(file) {
    if (!file) throw new UserError("Alege o imagine PNG sau BMP.");
    if (file.size === 0) throw new UserError("Fișierul ales este gol.");
    if (file.size > MAX_FILE_BYTES) throw new UserError("Alege o imagine de cel mult 10 MiB.");
    const header = new Uint8Array(await file.slice(0, 8).arrayBuffer());
    const isPNG = header.length >= 8 && [137, 80, 78, 71, 13, 10, 26, 10].every((value, index) => header[index] === value);
    const isBMP = header.length >= 2 && header[0] === 66 && header[1] === 77;
    if (!isPNG && !isBMP) throw new UserError("Format neacceptat. Alege o imagine PNG sau BMP.");
  }

  function updateMessageCapacity() {
    const bytes = byteLength($("message").value);
    const characters = Array.from($("message").value).length;
    const capacity = state.embed.capacity?.max_payload_bytes;
    $("message-counter").textContent = `${formatNumber(bytes)} octeți · ${formatNumber(characters)} caractere`;
    const hasCapacity = Number.isSafeInteger(capacity) && capacity >= 0;
    const overflow = hasCapacity && bytes > capacity;
    const ratio = hasCapacity && capacity > 0 ? bytes / capacity : bytes > 0 && hasCapacity ? 1 : 0;
    $("capacity-progress").value = Math.min(ratio * 100, 100);
    $("capacity-percent").textContent = hasCapacity ? `${formatNumber(Math.ceil(ratio * 100))}%` : "—";
    $("capacity-progress").parentElement.classList.toggle("over-capacity", overflow);
    $("message-counter").classList.toggle("text-danger", overflow);
    $("message").classList.toggle("invalid", overflow);
    $("message").setAttribute("aria-invalid", String(overflow));
    $("capacity-description").classList.toggle("text-danger", overflow);
    $("capacity-description").textContent = overflow ?
      `Ai depășit limita cu ${formatNumber(bytes - capacity)} octeți. Scurtează mesajul sau alege o imagine mai mare.` :
      hasCapacity ? `${formatNumber(capacity - bytes)} octeți rămași.` :
      "Unele caractere, inclusiv diacriticele, ocupă mai mulți octeți.";
  }

  function updatePasswordGuidance() {
    const password = $("embed-password").value;
    const length = Array.from(password).length;
    const bytes = byteLength(password);
    const guidance = $("password-guidance");
    const invalid = password.length > 0 && (length < 12 || bytes > MAX_PASSWORD_BYTES);
    guidance.classList.toggle("text-danger", invalid);
    $("embed-password").setAttribute("aria-invalid", String(invalid));
    if (!password) guidance.textContent = "Alege o frază unică. Parola nu poate fi recuperată.";
    else if (bytes > MAX_PASSWORD_BYTES) guidance.textContent = "Parola depășește limita de 1.024 octeți UTF-8.";
    else if (length < 12) guidance.textContent = `Mai adaugă ${12 - length} ${length === 11 ? "caracter" : "caractere"}.`;
    else if (length < 20) guidance.textContent = "Lungime acceptată. Recomandăm o frază unică de cel puțin 20 de caractere.";
    else guidance.textContent = "Lungime recomandată. Păstrează parola în siguranță.";
  }

  async function loadCapacity(file, version) {
    const controller = new AbortController();
    state.embed.capacityController = controller;
    $("capacity-summary").textContent = "Se verifică imaginea…";
    const data = new FormData();
    data.append("image", file);
    try {
      const result = await request("/api/v1/capacity", data, controller);
      if (version !== state.embed.version) return;
      if (!Number.isSafeInteger(result.max_payload_bytes) || result.max_payload_bytes < 0 ||
          !Number.isSafeInteger(result.width) || !Number.isSafeInteger(result.height)) throw new UserError("Serverul a returnat o capacitate invalidă.");
      state.embed.capacity = result;
      $("capacity-summary").textContent = `${formatNumber(result.width)} × ${formatNumber(result.height)} px · ${formatNumber(result.max_payload_bytes)} octeți disponibili`;
      if (result.max_payload_bytes === 0) showError("embed-file-error", "Imaginea este prea mică pentru a ascunde un mesaj.");
      updateMessageCapacity();
    } catch (error) {
      if (version !== state.embed.version || (controller.signal.aborted && !(error instanceof UserError))) return;
      state.embed.capacity = null;
      $("capacity-summary").textContent = "";
      showError("embed-file-error", error);
      updateMessageCapacity();
    } finally {
      if (state.embed.capacityController === controller) state.embed.capacityController = null;
    }
  }

  async function chooseFile(kind, file) {
    if (state[kind].busy) return;
    if (kind === "embed") cancelDemo();
    const item = state[kind];
    const version = ++item.version;
    item.file = null;
    revokePreview(kind);
    hideError(`${kind}-error`);
    hideError(`${kind}-file-error`);
    if (kind === "embed") {
      state.embed.capacityController?.abort();
      state.embed.capacityController = null;
      state.embed.capacity = null;
      clearEmbedResult();
      $("capacity-summary").textContent = "";
      updateMessageCapacity();
    } else clearExtractResult();
    if (!file) return;
    try {
      await validateFile(file);
      if (version !== item.version) return;
      item.file = file;
      item.preview = URL.createObjectURL(file);
      $(`${kind}-preview`).src = item.preview;
      $(`${kind}-preview-wrap`).hidden = false;
      $(`${kind}-empty`).hidden = true;
      $(`${kind}-file-summary`).hidden = false;
      $(`${kind}-file-name`).textContent = `${file.name} · ${(file.size / 1024).toLocaleString("ro-RO", { maximumFractionDigits: 1 })} KiB`;
      if (kind === "embed") await loadCapacity(file, version);
    } catch (error) {
      if (version !== item.version) return;
      $(`${kind}-image`).value = "";
      showError(`${kind}-file-error`, error);
    }
  }

  async function loadDemo() {
    if (state.embed.busy || state.embed.demoController) return;
    const controller = new AbortController();
    const version = state.embed.version;
    state.embed.demoController = controller;
    hideError("embed-error");
    hideError("embed-file-error");
    updateDemoButton();
    const timeout = setTimeout(() => controller.abort(), 15_000);
    const isCurrent = () => state.embed.demoController === controller && state.embed.version === version && !state.embed.busy;
    try {
      const response = await fetch("/demo-cover.png", {
        signal: controller.signal, credentials: "same-origin", redirect: "error",
      });
      if (!response.ok || response.headers.get("content-type")?.split(";")[0].trim() !== "image/png") throw new Error("Demo image unavailable");
      const blob = await response.blob();
      if (!isCurrent()) return;
      $("embed-image").value = "";
      await chooseFile("embed", new File([blob], "imagine-demonstrativă.png", { type: "image/png" }));
    } catch {
      if (isCurrent()) showError("embed-file-error", "Imaginea demonstrativă nu s-a încărcat. Încearcă din nou sau alege un fișier.");
    } finally {
      clearTimeout(timeout);
      if (state.embed.demoController === controller) {
        state.embed.demoController = null;
        updateDemoButton();
      }
    }
  }

  function connectUpload(kind) {
    const input = $(`${kind}-image`);
    const zone = $(`${kind}-dropzone`);
    input.addEventListener("change", () => { void chooseFile(kind, input.files?.[0]); });
    for (const event of ["dragenter", "dragover"]) zone.addEventListener(event, (e) => {
      e.preventDefault();
      if (!state[kind].busy) { zone.classList.add("drag-over"); if (e.dataTransfer) e.dataTransfer.dropEffect = "copy"; }
    });
    for (const event of ["dragleave", "drop"]) zone.addEventListener(event, (e) => {
      e.preventDefault();
      zone.classList.remove("drag-over");
    });
    zone.addEventListener("drop", (e) => {
      if (state[kind].busy) return;
      const files = e.dataTransfer?.files;
      if (!files?.length) return;
      input.value = "";
      if (files.length !== 1) {
        if (kind === "embed") cancelDemo();
        showError(`${kind}-file-error`, "Încarcă o singură imagine.");
        return;
      }
      void chooseFile(kind, files[0]);
    });
    $(`remove-${kind}-image`).addEventListener("click", () => { input.value = ""; void chooseFile(kind, null); });
  }

  function validPassword(password, embedding) {
    if (!password) throw new UserError("Introdu parola pentru a continua.");
    if (embedding && Array.from(password).length < 12) throw new UserError("Parola de criptare trebuie să aibă minimum 12 caractere.");
    if (byteLength(password) > MAX_PASSWORD_BYTES) throw new UserError("Parola trebuie să aibă maximum 1.024 octeți UTF-8.");
  }

  $("embed-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.embed.busy) return;
    hideError("embed-error");
    const message = $("message").value;
    const password = $("embed-password").value;
    try {
      if (!state.embed.file) throw new UserError("Încarcă o imagine PNG sau BMP înainte de criptare.");
      if (!state.embed.capacity) throw new UserError(state.embed.capacityController ? "Așteaptă verificarea capacității imaginii." : "Capacitatea imaginii nu este disponibilă. Alege din nou imaginea.");
      if (!message.length) throw new UserError("Introdu mesajul pe care vrei să-l ascunzi.");
      if (byteLength(message) > state.embed.capacity.max_payload_bytes) throw new UserError("Mesajul depășește capacitatea imaginii. Scurtează-l sau alege o imagine mai mare.");
      validPassword(password, true);
    } catch (error) { showError("embed-error", error); return; }
    hideError("embed-file-error");
    clearEmbedResult();
    const original = state.embed.file;
    const operation = ++state.embed.operation;
    const controller = new AbortController();
    state.embed.controller = controller;
    const data = new FormData();
    data.append("image", original);
    // File parts preserve exact UTF-8 bytes; string FormData fields normalize
    // LF to CRLF and would otherwise change multiline text and its capacity.
    data.append("message", new Blob([encoder.encode(message)], { type: "text/plain;charset=utf-8" }), "message.txt");
    data.append("password", password);
    setFormBusy("embed", true);
    try {
      const blob = await request("/api/v1/embed", data, controller, true);
      if (operation !== state.embed.operation) return;
      state.result.blob = blob;
      state.result.original = original;
      state.result.url = URL.createObjectURL(blob);
      $("result-image").src = state.result.url;
      $("download-image").href = state.result.url;
      const safeStem = original.name.replace(/\.[^.]*$/, "").replace(/[^\p{L}\p{N}_-]+/gu, "-").slice(0, 80) || "imagine";
      $("download-image").download = `${safeStem}-stegvault.png`;
      setEmbedPreview("result");
      $("embed-result").hidden = false;
      $("embed-password").value = "";
      resetPasswordVisibility("embed-password");
      updatePasswordGuidance();
      focusResult("embed", $("embed-result-heading"));
    } catch (error) {
      if (operation === state.embed.operation && (!controller.signal.aborted || error instanceof UserError)) showError("embed-error", error);
    } finally {
      if (operation === state.embed.operation) { state.embed.controller = null; setFormBusy("embed", false); }
    }
  });

  $("extract-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.extract.busy) return;
    hideError("extract-error");
    clearExtractResult();
    const password = $("extract-password").value;
    try {
      if (!state.extract.file) throw new UserError("Încarcă imaginea cu mesaj ascuns.");
      validPassword(password, false);
    } catch (error) { showError("extract-error", error); return; }
    hideError("extract-file-error");
    const operation = ++state.extract.operation;
    const controller = new AbortController();
    state.extract.controller = controller;
    const data = new FormData();
    data.append("image", state.extract.file);
    data.append("password", password);
    setFormBusy("extract", true);
    try {
      const result = await request("/api/v1/extract", data, controller);
      if (operation !== state.extract.operation) return;
      if (typeof result.message !== "string") throw new UserError("Mesajul extras este invalid.");
      $("decrypted-message").textContent = result.message;
      $("extracted-byte-count").textContent = `${formatNumber(byteLength(result.message))} octeți UTF-8 · ${formatNumber(Array.from(result.message).length)} caractere`;
      $("extract-result").hidden = false;
      $("extract-password").value = "";
      resetPasswordVisibility("extract-password");
      focusResult("extract", $("decrypted-message"));
    } catch (error) {
      if (operation === state.extract.operation && (!controller.signal.aborted || error instanceof UserError)) showError("extract-error", error);
    } finally {
      if (operation === state.extract.operation) { state.extract.controller = null; setFormBusy("extract", false); }
    }
  });

  $("analyze-button").addEventListener("click", async () => {
    if (!state.result.blob || !state.result.original || state.result.controller) return;
    hideError("analyze-error");
    $("analysis-results").hidden = true;
    const version = state.result.version;
    const controller = new AbortController();
    state.result.controller = controller;
    const data = new FormData();
    data.append("original", state.result.original);
    data.append("stego", state.result.blob, "stegvault.png");
    setButtonBusy($("analyze-button"), true, "Se calculează metricile…");
    try {
      const result = await request("/api/v1/analyze", data, controller);
      if (version !== state.result.version) return;
      if (typeof result.ssim !== "number" || !Number.isFinite(result.ssim) ||
          (!result.psnr_infinite && (typeof result.psnr_db !== "number" || !Number.isFinite(result.psnr_db)))) throw new UserError("Serverul a returnat metrici invalide.");
      $("metric-psnr").textContent = result.psnr_infinite ? "∞ dB" : `${result.psnr_db.toLocaleString("ro-RO", { maximumFractionDigits: 2 })} dB`;
      $("metric-ssim").textContent = result.ssim.toLocaleString("ro-RO", { minimumFractionDigits: 4, maximumFractionDigits: 6 });
      $("chi-square-output").textContent = JSON.stringify(result.chi_square ?? {}, null, 2);
      $("analysis-results").hidden = false;
    } catch (error) {
      if (version === state.result.version && (!controller.signal.aborted || error instanceof UserError)) showError("analyze-error", error);
    } finally {
      if (version === state.result.version) { state.result.controller = null; setButtonBusy($("analyze-button"), false); }
    }
  });

  $("message").addEventListener("input", () => { clearEmbedResult(); hideError("embed-error"); updateMessageCapacity(); });
  $("embed-password").addEventListener("input", () => { clearEmbedResult(); hideError("embed-error"); updatePasswordGuidance(); });
  $("extract-password").addEventListener("input", () => { clearExtractResult(); hideError("extract-error"); });

  for (const button of document.querySelectorAll(".show-password")) button.addEventListener("click", () => {
    const input = $(button.dataset.passwordTarget);
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    button.setAttribute("aria-pressed", String(show));
    const operation = input.id === "embed-password" ? "criptare" : "decriptare";
    button.setAttribute("aria-label", `${show ? "Ascunde" : "Arată"} parola de ${operation}`);
  });

  function activateTab(id, focus = false) {
    for (const tab of document.querySelectorAll('[role="tab"]')) {
      const active = tab.id === id;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      $(tab.getAttribute("aria-controls")).hidden = !active;
      if (active && focus) tab.focus();
    }
  }

  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activateTab(tab.id));
    tab.addEventListener("keydown", (event) => {
      let next;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      if (next !== undefined) { event.preventDefault(); activateTab(tabs[next].id, true); }
    });
  });

  $("copy-message").addEventListener("click", async () => {
    const text = $("decrypted-message").textContent;
    if (!text || $("extract-result").hidden) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(text);
      $("copy-feedback").textContent = "Mesaj copiat. Golește clipboardul după utilizare.";
    } catch {
      $("copy-feedback").textContent = "Selectează mesajul și copiază-l manual.";
      $("decrypted-message").focus();
    }
  });

  $("clear-message").addEventListener("click", () => {
    clearExtractResult();
    $("extract-password").value = "";
    resetPasswordVisibility("extract-password");
    $("extract-password").focus();
  });

  function clearSession() {
    cancelDemo();
    for (const kind of ["embed", "extract"]) {
      state[kind].operation += 1;
      state[kind].version += 1;
      state[kind].controller?.abort();
      state[kind].controller = null;
      if (kind === "embed") {
        state.embed.capacityController?.abort();
        state.embed.capacityController = null;
        state.embed.capacity = null;
      }
      state[kind].file = null;
      revokePreview(kind);
      $(`${kind}-form`).reset();
      setFormBusy(kind, false);
      hideError(`${kind}-error`);
      hideError(`${kind}-file-error`);
      resetPasswordVisibility(`${kind}-password`);
      $(`${kind}-dropzone`).classList.remove("drag-over");
    }
    clearEmbedResult();
    clearExtractResult();
    $("capacity-summary").textContent = "";
    updateMessageCapacity();
    updatePasswordGuidance();
  }

  $("clear-session").addEventListener("click", () => {
    clearSession();
    clearTimeout(state.toastTimer);
    $("session-feedback").hidden = false;
    state.toastTimer = setTimeout(() => { $("session-feedback").hidden = true; }, 4200);
  });

  // Prevent dropping an image outside the designated zones from navigating away.
  document.addEventListener("dragover", (event) => { if (event.dataTransfer?.types?.includes("Files")) event.preventDefault(); });
  document.addEventListener("drop", (event) => { if (event.dataTransfer?.types?.includes("Files")) event.preventDefault(); });
  window.addEventListener("pagehide", () => { clearSession(); clearTimeout(state.toastTimer); });
  if (browserProcessing) {
    $("processing-note").textContent = "Imaginile, mesajele și parolele sunt procesate în acest browser.";
  }
  connectUpload("embed");
  connectUpload("extract");
  $("embed-demo")?.addEventListener("click", () => { void loadDemo(); });
  $("preview-original")?.addEventListener("click", () => setEmbedPreview("original"));
  $("preview-result")?.addEventListener("click", () => setEmbedPreview("result"));
  setEmbedPreview("original");
  updateMessageCapacity();
  updatePasswordGuidance();
})();
