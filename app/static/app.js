/*
  Small progressive enhancements. The application remains functional without JavaScript.
  Copyright © 2026 Gateway Information Group LLC. All rights reserved.
*/

(() => {
  "use strict";

  const body = document.body;
  const menuButton = document.querySelector("[data-menu-toggle]");
  const sidebar = document.querySelector(".sidebar");

  const closeMenu = () => {
    body.classList.remove("menu-open");
    menuButton?.setAttribute("aria-expanded", "false");
  };

  menuButton?.setAttribute("aria-expanded", "false");
  menuButton?.addEventListener("click", (event) => {
    event.stopPropagation();
    const open = body.classList.toggle("menu-open");
    menuButton.setAttribute("aria-expanded", String(open));
  });

  document.addEventListener("click", (event) => {
    if (!body.classList.contains("menu-open")) return;
    if (sidebar?.contains(event.target) || menuButton?.contains(event.target)) return;
    closeMenu();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenu();
  });

  document.querySelectorAll("[data-dismiss]").forEach((button) => {
    button.addEventListener("click", () => button.closest(".flash")?.remove());
  });

  document.querySelectorAll("[data-auto-submit]").forEach((select) => {
    select.addEventListener("change", () => select.form?.submit());
  });

  document.querySelectorAll(".file-drop").forEach((drop) => {
    const input = drop.querySelector('input[type="file"]');
    const strong = drop.querySelector("strong");
    const originalText = strong?.textContent || "Select file";

    ["dragenter", "dragover"].forEach((name) => {
      drop.addEventListener(name, (event) => {
        event.preventDefault();
        drop.classList.add("dragover");
      });
    });

    ["dragleave", "drop"].forEach((name) => {
      drop.addEventListener(name, () => drop.classList.remove("dragover"));
    });

    input?.addEventListener("change", () => {
      if (!strong) return;
      strong.textContent = input.files?.[0]?.name || originalText;
    });
  });

  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", () => {
      const submitters = form.querySelectorAll('button[type="submit"]');
      submitters.forEach((button) => {
        if (!button.disabled) {
          button.dataset.originalText = button.textContent || "";
          button.setAttribute("aria-busy", "true");
        }
      });
    });
  });
})();

// Workflow Studio JSON helpers and stage reordering. The JSON remains the executable source.
document.addEventListener("DOMContentLoaded", () => {
  const editor = document.querySelector("#workflow-config-editor");
  document.querySelectorAll("[data-format-json]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!editor) return;
      try { editor.value = JSON.stringify(JSON.parse(editor.value), null, 2); }
      catch (error) { window.alert(`JSON cannot be formatted: ${error.message}`); }
    });
  });

  const canvas = document.querySelector("[data-workflow-canvas]");
  if (canvas && editor) {
    let dragged = null;
    const cards = () => [...canvas.querySelectorAll(".workflow-stage")];
    cards().forEach((card) => {
      card.addEventListener("dragstart", () => { dragged = card; card.classList.add("dragging"); });
      card.addEventListener("dragend", () => { card.classList.remove("dragging"); dragged = null; });
      card.addEventListener("dragover", (event) => {
        event.preventDefault();
        if (!dragged || dragged === card) return;
        const rect = card.getBoundingClientRect();
        const after = event.clientX > rect.left + rect.width / 2;
        canvas.insertBefore(dragged, after ? card.nextSibling : card);
      });
    });
    canvas.addEventListener("drop", (event) => {
      event.preventDefault();
      try {
        const config = JSON.parse(editor.value);
        const order = cards().map((card) => card.dataset.stageKey);
        const byKey = new Map((config.stages || []).map((stage) => [stage.key, stage]));
        config.stages = order.map((key) => byKey.get(key)).filter(Boolean);
        editor.value = JSON.stringify(config, null, 2);
        cards().forEach((card, index) => { const marker = card.querySelector(".stage-order"); if (marker) marker.textContent = String(index + 1); });
      } catch (_) { /* Leave editor untouched when manually invalid JSON is present. */ }
    });
  }

  const selectAll = document.querySelector("[data-select-all]");
  selectAll?.addEventListener("change", () => {
    document.querySelectorAll('input[name="references"]').forEach((box) => { box.checked = selectAll.checked; });
  });
});

// Lightweight visual builders write into the same validated JSON model; they do not create a parallel engine.
document.addEventListener("DOMContentLoaded", () => {
  const editor = document.querySelector("#workflow-config-editor");
  const parseConfig = () => editor ? JSON.parse(editor.value) : null;
  const writeConfig = (config) => { if (editor) editor.value = JSON.stringify(config, null, 2); };
  document.querySelector("[data-add-stage]")?.addEventListener("click", () => {
    try {
      const config = parseConfig(); const root = document.querySelector("[data-stage-builder]");
      const key = root.querySelector("[data-stage-key]").value.trim();
      const label = root.querySelector("[data-stage-label]").value.trim();
      const terminal = root.querySelector("[data-stage-terminal]").checked;
      if (!key || !label) throw new Error("Stage key and label are required.");
      if ((config.stages || []).some((stage) => stage.key === key)) throw new Error("That stage key already exists.");
      const stage = {key, label, description: "", terminal};
      if (!terminal) { stage.default_role = root.querySelector("[data-stage-role]").value.trim() || "Case Manager"; stage.sla_hours = Number(root.querySelector("[data-stage-sla]").value || 0); }
      config.stages = [...(config.stages || []), stage]; writeConfig(config);
    } catch (error) { window.alert(error.message); }
  });
  document.querySelector("[data-add-transition]")?.addEventListener("click", () => {
    try {
      const config = parseConfig(); const root = document.querySelector("[data-transition-builder]");
      const from = root.querySelector("[data-transition-from]").value.trim(); const to = root.querySelector("[data-transition-to]").value.trim();
      const label = root.querySelector("[data-transition-label]").value.trim(); const roles = root.querySelector("[data-transition-roles]").value.split(",").map((x) => x.trim()).filter(Boolean);
      if (!from || !to || !label || !roles.length) throw new Error("From, to, label, and at least one role are required.");
      config.transitions = [...(config.transitions || []), {from, to, label, roles}]; writeConfig(config);
    } catch (error) { window.alert(error.message); }
  });
});
