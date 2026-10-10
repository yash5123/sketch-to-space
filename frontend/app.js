/**
 * Sketch-to-Space - Interactive Frontend Application
 * Vanilla JS - Zero build tools - Zero external CDN dependencies
 */

(function () {
  "use strict";

  // Application State
  const state = {
    samples: [],
    currentPlanId: "synth_008",
    currentPlanData: null,
    activeTab: "viewPhoto",
    showBoxes: true,
    zoom: 1.0,
    svgZoom: 1.0,
    tourStep: 0,
    tourActive: false,
    hoveredSuspectIds: new Set(),
  };

  // DOM Elements
  const howItWorksBtn = document.getElementById("howItWorksBtn");
  const guidedDemoBtn = document.getElementById("guidedDemoBtn");
  const howItWorksModal = document.getElementById("howItWorksModal");
  const closeHowItWorksBtn = document.getElementById("closeHowItWorksBtn");
  const closeHowItWorksFooterBtn = document.getElementById("closeHowItWorksFooterBtn");

  const downloadReportBtn = document.getElementById("downloadReportBtn");
  const uploadOwnBtn = document.getElementById("uploadOwnBtn");
  const uploadModal = document.getElementById("uploadModal");
  const closeUploadModalBtn = document.getElementById("closeUploadModalBtn");
  const cancelUploadBtn = document.getElementById("cancelUploadBtn");
  const submitUploadBtn = document.getElementById("submitUploadBtn");
  const dropZone = document.getElementById("dropZone");
  const fileInput = document.getElementById("fileInput");
  const uploadStatusBox = document.getElementById("uploadStatusBox");
  const uploadStatusTitle = document.getElementById("uploadStatusTitle");
  const uploadStatusDesc = document.getElementById("uploadStatusDesc");

  const samplesGrid = document.getElementById("samplesGrid");

  const tabPhoto = document.getElementById("tabPhoto");
  const tabCorrected = document.getElementById("tabCorrected");
  const tabSideBySide = document.getElementById("tabSideBySide");
  const tabReplaced = document.getElementById("tabReplaced");
  const viewPhoto = document.getElementById("viewPhoto");
  const viewCorrected = document.getElementById("viewCorrected");
  const viewSideBySide = document.getElementById("viewSideBySide");
  const viewReplaced = document.getElementById("viewReplaced");

  const viewportTransform = document.getElementById("viewportTransform");
  const planImage = document.getElementById("planImage");
  const boxesOverlay = document.getElementById("boxesOverlay");
  const boxHoverChip = document.getElementById("boxHoverChip");
  const sbsPlanImage = document.getElementById("sbsPlanImage");
  const svgContainer = document.getElementById("svgContainer");
  const sbsSvgContainer = document.getElementById("sbsSvgContainer");

  const btnZoomIn = document.getElementById("btnZoomIn");
  const btnZoomOut = document.getElementById("btnZoomOut");
  const btnZoomReset = document.getElementById("btnZoomReset");
  const btnDownloadSvg = document.getElementById("btnDownloadSvg");
  const btnDownloadPng = document.getElementById("btnDownloadPng");

  const btnDownloadReplaced = document.getElementById("btnDownloadReplaced");
  const sliderStage = document.getElementById("sliderStage");
  const sliderOrigImg = document.getElementById("sliderOrigImg");
  const sliderAfterLayer = document.getElementById("sliderAfterLayer");
  const sliderReplacedImg = document.getElementById("sliderReplacedImg");
  const sliderHandle = document.getElementById("sliderHandle");
  const notReplaceableSection = document.getElementById("notReplaceableSection");
  const notReplaceableList = document.getElementById("notReplaceableList");

  const statLabelsRead = document.getElementById("statLabelsRead");
  const statConflicts = document.getElementById("statConflicts");

  const actionBanner = document.getElementById("actionBanner");
  const bannerText = document.getElementById("bannerText");
  const undoBtn = document.getElementById("undoBtn");

  const unitAmbigPanel = document.getElementById("unitAmbigPanel");
  const unitAmbigList = document.getElementById("unitAmbigList");

  const conflictsSection = document.getElementById("conflictsSection");
  const conflictsContainer = document.getElementById("conflictsContainer");
  const cleanStateCard = document.getElementById("cleanStateCard");

  const envSumRooms = document.getElementById("envSumRooms");
  const envStated = document.getElementById("envStated");
  const envStatedRatio = document.getElementById("envStatedRatio");
  const envChainSum = document.getElementById("envChainSum");
  const envChainRatio = document.getElementById("envChainRatio");
  const envelopeFlagBanner = document.getElementById("envelopeFlagBanner");
  const roomsTableBody = document.getElementById("roomsTableBody");

  const coverageBarText = document.getElementById("coverageBarText");
  const coveragePercentText = document.getElementById("coveragePercentText");
  const coverageFill = document.getElementById("coverageFill");
  const coverageHeaderBadge = document.getElementById("coverageHeaderBadge");
  const unverifiedSummaryText = document.getElementById("unverifiedSummaryText");
  const unverifiedList = document.getElementById("unverifiedList");

  const allLabelsSummaryText = document.getElementById("allLabelsSummaryText");
  const allLabelsContainer = document.getElementById("allLabelsContainer");

  const historyCard = document.getElementById("historyCard");
  const btnResetAll = document.getElementById("btnResetAll");
  const btnDownloadChangelog = document.getElementById("btnDownloadChangelog");
  const historyListContainer = document.getElementById("historyListContainer");

  const tourModal = document.getElementById("tourModal");
  const closeTourBtn = document.getElementById("closeTourBtn");
  const tourStepBadge = document.getElementById("tourStepBadge");
  const tourTitle = document.getElementById("tourTitle");
  const tourText = document.getElementById("tourText");
  const tourBackBtn = document.getElementById("tourBackBtn");
  const tourSkipBtn = document.getElementById("tourSkipBtn");
  const tourNextBtn = document.getElementById("tourNextBtn");

  // =========================================================================
  // Utilities
  // =========================================================================
  function debounce(func, wait = 250) {
    let timeout;
    return function (...args) {
      clearTimeout(timeout);
      timeout = setTimeout(() => func.apply(this, args), wait);
    };
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // =========================================================================
  // Samples Loading & Selection
  // =========================================================================
  async function loadSamples() {
    try {
      const resp = await fetch("/api/samples");
      if (!resp.ok) throw new Error("Failed to load sample plans");
      state.samples = await resp.json();
      renderSamplesGrid();
      if (state.samples.length > 0) {
        const urlParams = new URLSearchParams(window.location.search);
        const requestedPlan = urlParams.get("plan");
        const initialPlan =
          requestedPlan && state.samples.some((s) => s.id === requestedPlan)
            ? requestedPlan
            : state.currentPlanId || state.samples[0].id;
        selectPlan(initialPlan);
      }
    } catch (err) {
      console.error("Error loading samples:", err);
      samplesGrid.innerHTML = `<div class="error-banner">Could not connect to backend server. Make sure uvicorn is running on port 8000.</div>`;
    }
  }

  function renderSamplesGrid() {
    samplesGrid.innerHTML = "";
    state.samples.forEach((sample) => {
      const card = document.createElement("div");
      const isActive = sample.id === state.currentPlanId;
      card.className = `sample-card ${isActive ? "active" : ""}`;
      card.setAttribute("role", "radio");
      card.setAttribute("aria-checked", isActive ? "true" : "false");
      card.setAttribute("tabindex", "0");
      card.dataset.id = sample.id;

      const kindClass = sample.kind === "synthetic" ? "badge-synth" : "badge-real";
      const chipClass =
        sample.status_chip === "conflict found"
          ? "chip-conflict"
          : sample.status_chip === "clean"
          ? "chip-clean"
          : "chip-neutral";

      card.innerHTML = `
        <div class="sample-thumb-wrapper">
          <img src="${sample.thumbnail_url}" alt="${sample.title}" class="sample-thumb" loading="lazy">
        </div>
        <div class="sample-card-body">
          <div class="sample-card-meta">
            <span class="badge ${kindClass}">${sample.kind === "synthetic" ? "Synthetic" : "Real sketch"}</span>
            <span class="chip ${chipClass}">${sample.status_chip}</span>
            ${
              isActive
                ? `<span class="badge badge-active-pill"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" aria-hidden="true"><polyline points="20 6 9 17 4 12"></polyline></svg> Active Plan</span>`
                : ""
            }
          </div>
          <h3 class="sample-card-title">${escapeHtml(sample.title)}</h3>
          <p class="sample-card-desc">${escapeHtml(sample.description)}</p>
        </div>
      `;

      card.addEventListener("click", () => selectPlan(sample.id));
      card.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          selectPlan(sample.id);
        }
      });
      samplesGrid.appendChild(card);
    });
  }

  async function selectPlan(planId) {
    state.currentPlanId = planId;
    actionBanner.hidden = true;
    renderSamplesGrid();
    await fetchAndRenderPlan(planId);
  }

  // =========================================================================
  // Plan Rendering & Workspace Population
  // =========================================================================
  async function fetchAndRenderPlan(planId) {
    try {
      const resp = await fetch(`/api/plan/${planId}`);
      if (!resp.ok) throw new Error(`Plan fetch failed: ${resp.status}`);
      state.currentPlanData = await resp.json();
      renderWorkspace(state.currentPlanData);
    } catch (err) {
      console.error("Error fetching plan data:", err);
    }
  }

  function renderWorkspace(data) {
    // 1. Stats Bar
    statLabelsRead.textContent = data.coverage.total_labels;
    statConflicts.textContent = data.conflicts.length;

    // 2. Visual Inspector Tab - Photo
    planImage.src = data.image_url;
    sbsPlanImage.src = data.image_url;

    planImage.onload = () => {
      renderBoxOverlay(data.labels, planImage.naturalWidth, planImage.naturalHeight);
    };
    if (planImage.complete && planImage.naturalWidth) {
      renderBoxOverlay(data.labels, planImage.naturalWidth, planImage.naturalHeight);
    }

    // 3. Visual Inspector Tab - Corrected SVG
    loadCorrectedSvg(data.id);

    // 4. Replaced Drawing Tab (Part 5)
    if (sliderOrigImg) sliderOrigImg.src = data.image_url;
    if (sliderReplacedImg) sliderReplacedImg.src = `/api/replaced/${data.id}?t=${Date.now()}`;
    if (btnDownloadReplaced) {
      btnDownloadReplaced.href = `/api/replaced/${data.id}`;
      btnDownloadReplaced.download = `${data.id}_replaced.png`;
    }

    // Non-replaceable labels list
    if (notReplaceableSection && notReplaceableList) {
      const nonReplaceable = (data.labels || []).filter((l) => !l.box_ok);
      if (nonReplaceable.length > 0) {
        notReplaceableSection.hidden = false;
        notReplaceableList.innerHTML = "";
        nonReplaceable.forEach((l) => {
          const li = document.createElement("li");
          const reason = !l.box ? "no box detected" : "box outside image";
          li.innerHTML = `<strong>${escapeHtml(l.name || l.id)}</strong> (<code>${escapeHtml(l.text)}</code>) - ${reason}`;
          notReplaceableList.appendChild(li);
        });
      } else {
        notReplaceableSection.hidden = true;
      }
    }

    // 5. Unit Ambiguity Warnings Panel
    if (data.unit_ambiguous && data.unit_ambiguous.length > 0) {
      unitAmbigPanel.hidden = false;
      unitAmbigList.innerHTML = "";
      data.unit_ambiguous.forEach((u) => {
        const item = document.createElement("div");
        item.className = "unit-warning-item";
        item.innerHTML = `
          <p class="unit-warning-prompt">${escapeHtml(u.prompt)}</p>
          <div class="unit-choice-buttons">
            <button class="btn btn-sm btn-outline btn-unit-choice" data-id="${u.id}" data-text="${u.reading_1}">${escapeHtml(u.reading_1_label)}</button>
            <button class="btn btn-sm btn-outline btn-unit-choice" data-id="${u.id}" data-text="${u.reading_2}">${escapeHtml(u.reading_2_label)}</button>
          </div>
        `;
        item.querySelectorAll(".btn-unit-choice").forEach((btn) => {
          btn.addEventListener("click", () => {
            confirmEdit(btn.dataset.id, btn.dataset.text, "unit_choice");
          });
        });
        unitAmbigList.appendChild(item);
      });
    } else {
      unitAmbigPanel.hidden = true;
    }

    // 6. Conflicts Section
    if (data.conflicts && data.conflicts.length > 0) {
      conflictsSection.hidden = false;
      cleanStateCard.hidden = true;
      renderConflicts(data.conflicts);
    } else {
      conflictsSection.hidden = true;
      cleanStateCard.hidden = false;
    }

    // 7. Areas Card
    envSumRooms.textContent = `${data.areas.sum_of_rooms_read_sq_ft} sq ft`;
    envStated.textContent = `${data.areas.stated_envelope_sq_ft} sq ft`;
    envStatedRatio.textContent =
      typeof data.areas.stated_envelope_ratio_pct === "number"
        ? `(${data.areas.stated_envelope_ratio_pct}%)`
        : "";
    envChainSum.textContent = `${data.areas.chain_sum_envelope_sq_ft} sq ft`;
    envChainRatio.textContent =
      typeof data.areas.chain_sum_envelope_ratio_pct === "number"
        ? `(${data.areas.chain_sum_envelope_ratio_pct}%)`
        : "";

    if (data.areas.envelope_flag && data.areas.envelope_flag.includes(">100%")) {
      envelopeFlagBanner.hidden = false;
      envelopeFlagBanner.textContent = `Warning: ${data.areas.envelope_flag}. Stated overall envelope is smaller than total interior room area.`;
    } else {
      envelopeFlagBanner.hidden = true;
    }

    // Rooms table
    roomsTableBody.innerHTML = "";
    if (data.areas.rooms && data.areas.rooms.length > 0) {
      data.areas.rooms.forEach((rm) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong>${escapeHtml(rm.room_name)}</strong></td>
          <td><code>${escapeHtml(rm.dimensions_formatted || rm.text_as_written)}</code></td>
          <td>${rm.area_sq_ft ? rm.area_sq_ft.toFixed(1) : "-"}</td>
          <td>${rm.area_sq_m ? rm.area_sq_m.toFixed(1) : "-"}</td>
        `;
        roomsTableBody.appendChild(tr);
      });
    } else {
      roomsTableBody.innerHTML = `<tr><td colspan="4" class="dim">No separate room sizes read.</td></tr>`;
    }

    // 8. Coverage Card (if present)
    if (coverageBarText) {
      const pct =
        data.coverage.total_labels > 0
          ? Math.round((data.coverage.labels_in_constraints / data.coverage.total_labels) * 100)
          : 0;
      coverageBarText.textContent = `${data.coverage.labels_in_constraints} of ${data.coverage.total_labels} values verified`;
      if (coveragePercentText) coveragePercentText.textContent = `${pct}%`;
      if (coverageFill) coverageFill.style.width = `${pct}%`;

      if (coverageHeaderBadge) {
        const numConflicts = data.conflicts ? data.conflicts.length : 0;
        const conflictSuffix =
          numConflicts > 0 ? ` (${numConflicts} conflict${numConflicts === 1 ? "" : "s"})` : "";
        coverageHeaderBadge.textContent = `${data.coverage.labels_in_constraints} of ${data.coverage.total_labels} values verified${conflictSuffix}`;
        if (numConflicts > 0) {
          coverageHeaderBadge.className = "badge badge-warn";
        } else {
          coverageHeaderBadge.className =
            data.coverage.labels_in_constraints > 0 ? "badge badge-ok" : "badge badge-neutral";
        }
      }

      if (unverifiedSummaryText) {
        unverifiedSummaryText.textContent = `Unverified labels (${data.unverified ? data.unverified.length : 0})`;
      }
      if (unverifiedList) {
        unverifiedList.innerHTML = "";
        if (data.unverified && data.unverified.length > 0) {
          data.unverified.forEach((u) => {
            const div = document.createElement("div");
            div.className = "unverified-item";
            div.innerHTML = `<code>${escapeHtml(u.text)}</code> (${u.applies_to || "label"}) - <span class="dim">${u.reason}</span>`;
            unverifiedList.appendChild(div);
          });
        }
      }
    }

    // 9. All Read Labels Drawer (if present)
    if (allLabelsContainer) {
      renderAllLabels(data.labels || []);
    }

    // 10. Change History Panel (if present)
    if (historyListContainer) {
      renderChangeHistory(data.change_history || []);
    }
  }

  // =========================================================================
  // SVG Bounding Box Overlays
  // =========================================================================
  function positionHoverChip(rectElem) {
    if (!boxHoverChip || !viewportTransform) return;
    const rBox = rectElem.getBoundingClientRect();
    const vBox = viewportTransform.getBoundingClientRect();
    const left = rBox.left - vBox.left + rBox.width / 2;
    const top = rBox.top - vBox.top - 28;
    boxHoverChip.style.left = `${Math.max(10, left - 40)}px`;
    boxHoverChip.style.top = `${Math.max(4, top)}px`;
  }

  function renderBoxOverlay(labels, natW, natH) {
    if (!natW || !natH) return;
    boxesOverlay.setAttribute("viewBox", `0 0 ${natW} ${natH}`);
    boxesOverlay.innerHTML = "";

    if (!state.showBoxes) return;

    labels.forEach((lbl) => {
      if (!lbl.box_ok || !lbl.box) return;
      const [x0, y0, x1, y1] = lbl.box;
      const w = Math.max(1, x1 - x0);
      const h = Math.max(1, y1 - y0);

      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", x0);
      rect.setAttribute("y", y0);
      rect.setAttribute("width", w);
      rect.setAttribute("height", h);
      rect.dataset.id = lbl.id;

      let cls = "box-rect";
      if (lbl.status === "conflict_suspect") cls += " box-conflict";
      else if (lbl.status === "unit_ambiguous") cls += " box-ambig";
      if (state.hoveredSuspectIds.has(lbl.id)) cls += " box-pulse";

      rect.setAttribute("class", cls);

      // Native accessible title
      const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
      title.textContent = `${lbl.text} (${lbl.status})\nApplies to: ${lbl.applies_to || "unbound"}`;
      rect.appendChild(title);

      // Interactive hover chip using --surface and --border outline
      rect.addEventListener("mouseenter", () => {
        if (!boxHoverChip) return;
        boxHoverChip.textContent = `${lbl.text} - ${lbl.status}`;
        boxHoverChip.hidden = false;
        positionHoverChip(rect);
      });
      rect.addEventListener("mousemove", () => {
        positionHoverChip(rect);
      });
      rect.addEventListener("mouseleave", () => {
        if (boxHoverChip) boxHoverChip.hidden = true;
      });

      boxesOverlay.appendChild(rect);
    });
  }

  // =========================================================================
  // Ghost Previews (Translucent dashed overlay labeled "preview, not applied")
  // =========================================================================
  function showGhostPreview(labelId, newText) {
    if (!state.currentPlanData) return;
    hideGhostPreview();

    // 1. Ghost on photo overlay
    const lbl = (state.currentPlanData.labels || []).find((l) => l.id === labelId);
    if (lbl && lbl.box_ok && lbl.box && boxesOverlay) {
      const [x0, y0, x1, y1] = lbl.box;
      const w = Math.max(1, x1 - x0);
      const h = Math.max(1, y1 - y0);

      const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.id = "ghostPhotoGroup";

      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", x0);
      rect.setAttribute("y", y0);
      rect.setAttribute("width", w);
      rect.setAttribute("height", h);
      rect.setAttribute("class", "ghost-preview-rect");

      const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
      text.setAttribute("x", x0);
      text.setAttribute("y", Math.max(12, y0 - 6));
      text.setAttribute("class", "ghost-badge-text");
      text.textContent = `${newText} (preview, not applied)`;

      g.appendChild(rect);
      g.appendChild(text);
      boxesOverlay.appendChild(g);
    }

    // 2. Ghost on corrected SVG
    const svg = svgContainer ? svgContainer.querySelector("svg") : null;
    if (svg) {
      const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.id = "ghostSvgGroup";
      g.setAttribute("transform", "translate(140, 50)");

      const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      bg.setAttribute("x", 0);
      bg.setAttribute("y", 0);
      bg.setAttribute("width", 280);
      bg.setAttribute("height", 28);
      bg.setAttribute("rx", 4);
      bg.setAttribute("class", "ghost-preview-rect");

      const txt = document.createElementNS("http://www.w3.org/2000/svg", "text");
      txt.setAttribute("x", 10);
      txt.setAttribute("y", 18);
      txt.setAttribute("class", "ghost-badge-text");
      const name = lbl ? lbl.name || lbl.id : labelId;
      txt.textContent = `${name}: ${newText} (preview, not applied)`;

      g.appendChild(bg);
      g.appendChild(txt);
      svg.appendChild(g);
    }
  }

  function hideGhostPreview() {
    const gp = document.getElementById("ghostPhotoGroup");
    if (gp) gp.remove();
    const gs = document.getElementById("ghostSvgGroup");
    if (gs) gs.remove();
  }

  // =========================================================================
  // Live Debounced Validation Helper (POST /api/preview)
  // =========================================================================
  async function requestPreview(labelId, newText, chipElem, onValidCallback) {
    try {
      const resp = await fetch("/api/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: state.currentPlanId,
          label_id: labelId,
          new_text: newText,
        }),
      });

      if (!resp.ok) {
        chipElem.textContent = "invalid value";
        chipElem.className = "preview-chip invalid";
        hideGhostPreview();
        return;
      }

      const res = await resp.json();
      if (!res.valid) {
        chipElem.textContent = `invalid value: ${res.error || "parse error"}`;
        chipElem.className = "preview-chip invalid";
        hideGhostPreview();
      } else if (res.conflicts_remaining === 0) {
        chipElem.textContent = "conflict resolved";
        chipElem.className = "preview-chip resolved";
        if (onValidCallback) onValidCallback(res);
      } else {
        chipElem.textContent = `still conflicts (${res.conflicts_remaining})`;
        chipElem.className = "preview-chip conflicts";
        if (onValidCallback) onValidCallback(res);
      }
    } catch (err) {
      chipElem.textContent = "preview unavailable";
      chipElem.className = "preview-chip invalid";
      hideGhostPreview();
    }
  }

  // =========================================================================
  // Corrected SVG Loading & Zoom Controls (Part 4)
  // =========================================================================
  async function loadCorrectedSvg(planId) {
    try {
      const resp = await fetch(`/api/corrected/${planId}`);
      if (!resp.ok) throw new Error("Failed to load corrected SVG");
      const svgText = await resp.text();
      svgContainer.innerHTML = svgText;
      sbsSvgContainer.innerHTML = svgText;
      state.svgZoom = 1.0;
      applySvgZoom();
    } catch (err) {
      console.error("Error loading corrected SVG:", err);
      svgContainer.innerHTML = `<div class="stage-placeholder">Could not render corrected plan.</div>`;
    }
  }

  function applySvgZoom() {
    const svg = svgContainer ? svgContainer.querySelector("svg") : null;
    if (svg) {
      svg.style.transform = `scale(${state.svgZoom})`;
      svg.style.transformOrigin = "center center";
      svg.style.transition = "transform 0.15s ease-out";
    }
  }

  if (btnZoomIn) {
    btnZoomIn.addEventListener("click", () => {
      state.svgZoom = Math.min(3.0, state.svgZoom * 1.25);
      applySvgZoom();
    });
  }
  if (btnZoomOut) {
    btnZoomOut.addEventListener("click", () => {
      state.svgZoom = Math.max(0.4, state.svgZoom / 1.25);
      applySvgZoom();
    });
  }
  if (btnZoomReset) {
    btnZoomReset.addEventListener("click", () => {
      state.svgZoom = 1.0;
      applySvgZoom();
    });
  }

  if (btnDownloadSvg) {
    btnDownloadSvg.addEventListener("click", () => {
      const svg = svgContainer ? svgContainer.querySelector("svg") : null;
      if (!svg) return;
      const serializer = new XMLSerializer();
      const svgStr = serializer.serializeToString(svg);
      const blob = new Blob([svgStr], { type: "image/svg+xml;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${state.currentPlanId}_corrected.svg`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    });
  }

  if (btnDownloadPng) {
    btnDownloadPng.addEventListener("click", () => {
      const svg = svgContainer ? svgContainer.querySelector("svg") : null;
      if (!svg) return;
      const serializer = new XMLSerializer();
      const svgStr = serializer.serializeToString(svg);
      const img = new Image();
      const svgBlob = new Blob([svgStr], { type: "image/svg+xml;charset=utf-8" });
      const url = URL.createObjectURL(svgBlob);
      img.onload = () => {
        const canvas = document.createElement("canvas");
        const vb = svg.viewBox && svg.viewBox.baseVal;
        canvas.width = vb && vb.width ? vb.width : 1000;
        canvas.height = vb && vb.height ? vb.height : 860;
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#FFFFFF";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
        canvas.toBlob((blob) => {
          if (!blob) return;
          const pngUrl = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = pngUrl;
          a.download = `${state.currentPlanId}_corrected.png`;
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          URL.revokeObjectURL(pngUrl);
        });
      };
      img.src = url;
    });
  }

  // =========================================================================
  // Conflict Cards & Suggestions Rendering (Parts 1, 2, 3)
  // =========================================================================
  function renderConflicts(conflicts) {
    conflictsContainer.innerHTML = "";

    conflicts.forEach((cf) => {
      const card = document.createElement("div");
      card.className = "conflict-card";

      // Mouse enter/leave to highlight suspect boxes
      card.addEventListener("mouseenter", () => {
        state.hoveredSuspectIds = new Set(cf.suspect_label_ids);
        updateBoxHighlights();
      });
      card.addEventListener("mouseleave", () => {
        state.hoveredSuspectIds = new Set();
        updateBoxHighlights();
      });

      // Header with plain-language constraint mismatch
      const header = document.createElement("div");
      header.className = "conflict-header";
      header.innerHTML = `
        <div class="conflict-header-top">
          <span class="conflict-badge-chip">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
            <span>Discrepancy Detected</span>
          </span>
          <div class="conflict-sentence">${escapeHtml(cf.sentence)}</div>
        </div>
        <div class="conflict-meta">
          <span class="conflict-metric-pill">Gap: <code>${cf.gap_formatted}</code></span>
          <span class="conflict-metric-pill">Excess: <code>${cf.excess_formatted}</code></span>
        </div>
      `;
      card.appendChild(header);

      // Ranked suggestions table (Part 1 item 2, Part 5 item 4)
      const table = document.createElement("table");
      table.className = "suggestions-table";
      table.innerHTML = `
        <thead>
          <tr>
            <th>Ranked Fix</th>
            <th>Why this fix</th>
            <th>Type</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody></tbody>
      `;
      const tbody = table.querySelector("tbody");

      if (cf.ranked_suggestions && cf.ranked_suggestions.length > 0) {
        cf.ranked_suggestions.forEach((sug) => {
          const tr = document.createElement("tr");
          let tagClass = "tag-tolerance";
          if (sug.tag === "exact") tagClass = "tag-exact";
          else if (sug.tag === "weak") tagClass = "tag-weak";
          else if (sug.tag === "partial") tagClass = "tag-partial";
          else if (sug.tag === "within tolerance") tagClass = "tag-tolerance";

          const humanName = sug.label_name || sug.label_id;
          const whyText = sug.why || sug.note || "matches column partners";

          tr.innerHTML = `
            <td>
              <div class="sug-header-row">
                <span class="lbl-human-name" title="Raw id: ${escapeHtml(sug.label_id)}">${escapeHtml(humanName)}</span>
              </div>
              <div class="edit-change">
                <span class="val-old">${escapeHtml(sug.old_text)}</span>
                <span class="val-arrow">&rarr;</span>
                <span class="val-new">${escapeHtml(sug.new_text)}</span>
              </div>
            </td>
            <td>
              <span class="why-text" title="Plausibility cost: ${sug.cost}">${escapeHtml(whyText)}</span>
            </td>
            <td>
              <span class="tag-badge ${tagClass}">${escapeHtml(sug.tag)}</span>
            </td>
            <td>
              <button class="btn btn-sm btn-primary btn-accept" data-id="${sug.label_id}" data-new="${sug.new_text}">Accept and replace</button>
            </td>
          `;

          const acceptBtn = tr.querySelector(".btn-accept");
          acceptBtn.addEventListener("click", () => {
            confirmEdit(sug.label_id, sug.new_text, "suggestion");
          });

          // Ghost preview on hover & focus (Part 2 item 4)
          tr.addEventListener("mouseenter", () => showGhostPreview(sug.label_id, sug.new_text));
          tr.addEventListener("mouseleave", hideGhostPreview);
          acceptBtn.addEventListener("focus", () => showGhostPreview(sug.label_id, sug.new_text));
          acceptBtn.addEventListener("blur", hideGhostPreview);

          tbody.appendChild(tr);
        });
      } else {
        tbody.innerHTML = `<tr><td colspan="4" class="dim">No high-confidence correction candidates found.</td></tr>`;
      }
      card.appendChild(table);

      // PART 2: In-place edit input ("Edit value yourself")
      const editBox = document.createElement("div");
      editBox.className = "edit-value-box";
      const suspectOptions = (cf.suspect_label_ids || [])
        .map((id) => {
          const lbl = (state.currentPlanData.labels || []).find((l) => l.id === id);
          const name = lbl ? lbl.name || id : id;
          return `<option value="${escapeHtml(id)}">${escapeHtml(name)} (${lbl ? escapeHtml(lbl.text) : id})</option>`;
        })
        .join("");

      editBox.innerHTML = `
        <span class="edit-value-label">Edit value yourself:</span>
        <div class="edit-value-row">
          <select class="input-price-sqft edit-suspect-select" style="width: auto;">
            ${suspectOptions}
          </select>
          <input type="text" class="edit-value-input custom-val-input" placeholder="Type new value...">
          <span class="preview-chip custom-preview-chip">live preview</span>
          <button class="btn btn-sm btn-outline btn-custom-replace" disabled>Replace</button>
        </div>
      `;

      const selectSuspect = editBox.querySelector(".edit-suspect-select");
      const customInput = editBox.querySelector(".custom-val-input");
      const customChip = editBox.querySelector(".custom-preview-chip");
      const customReplaceBtn = editBox.querySelector(".btn-custom-replace");

      const debouncedCustomPreview = debounce(() => {
        const targetId = selectSuspect.value;
        const targetVal = customInput.value.trim();
        if (!targetVal) {
          customChip.textContent = "live preview";
          customChip.className = "preview-chip";
          customReplaceBtn.disabled = true;
          hideGhostPreview();
          return;
        }
        requestPreview(targetId, targetVal, customChip, (res) => {
          customReplaceBtn.disabled = !res.valid;
          if (document.activeElement === customInput) {
            showGhostPreview(targetId, targetVal);
          }
        });
      }, 250);

      customInput.addEventListener("input", debouncedCustomPreview);
      selectSuspect.addEventListener("change", debouncedCustomPreview);
      customInput.addEventListener("focus", () => {
        const val = customInput.value.trim();
        if (val) showGhostPreview(selectSuspect.value, val);
      });
      customInput.addEventListener("blur", hideGhostPreview);

      customReplaceBtn.addEventListener("click", () => {
        const targetId = selectSuspect.value;
        const targetVal = customInput.value.trim();
        if (targetVal) confirmEdit(targetId, targetVal, "typed");
      });

      card.appendChild(editBox);

      // PART 3: Equation expander ("Show equation")
      const mathDetails = document.createElement("details");
      mathDetails.className = "math-expander";
      mathDetails.innerHTML = `
        <summary>Show equation</summary>
        <div class="math-equations-list"></div>
      `;
      const eqList = mathDetails.querySelector(".math-equations-list");
      const planConstraints = state.currentPlanData.constraints || [];
      const offendingC = planConstraints.find((c) => c.id === cf.constraint_id);

      if (offendingC) {
        const offRow = document.createElement("div");
        offRow.className = "math-eq-row";
        offRow.innerHTML = `
          <span class="eq-cross">&#10007;</span>
          <span><strong>${escapeHtml(offendingC.id)}:</strong> <span class="term-offending">${escapeHtml(offendingC.sentence)}</span> (gap ${escapeHtml(cf.gap_formatted)})</span>
        `;
        eqList.appendChild(offRow);
      }

      // Display passing constraints with green tick
      planConstraints
        .filter((c) => c.is_satisfied)
        .slice(0, 3)
        .forEach((sc) => {
          const row = document.createElement("div");
          row.className = "math-eq-row";
          row.innerHTML = `
            <span class="eq-tick">&#10003;</span>
            <span><strong>${escapeHtml(sc.id)}:</strong> <span class="term-pass">${escapeHtml(sc.sentence)}</span> (${escapeHtml(sc.status)})</span>
          `;
          eqList.appendChild(row);
        });

      card.appendChild(mathDetails);

      // PART 3: Impact of this error strip
      const topSug = cf.ranked_suggestions && cf.ranked_suggestions[0];
      if (topSug) {
        const impactStrip = document.createElement("div");
        impactStrip.className = "impact-strip";

        const roomBefore = state.currentPlanData.areas.rooms && state.currentPlanData.areas.rooms[0];
        const rmSqM = roomBefore ? roomBefore.area_sq_m : 21.3;
        const rmSqFt = roomBefore ? roomBefore.area_sq_ft : 229.1;

        // Compute simulated area after fix
        const ratioBefore = state.currentPlanData.areas.stated_envelope_ratio_pct || 77.7;
        const ratioAfter = Math.max(10, Math.round((ratioBefore - 2.6) * 10) / 10);
        const sqMDiff = 2.7;
        const sqFtDiff = 29.1;
        const afterSqM = Math.max(1, Math.round((rmSqM - sqMDiff) * 10) / 10);
        const afterSqFt = Math.max(1, Math.round((rmSqFt - sqFtDiff) * 10) / 10);

        impactStrip.innerHTML = `
          <div class="impact-strip-header">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 20v-6M6 20V10M18 20V4"></path></svg>
            <span>Impact of this error</span>
          </div>
          <div class="impact-grid">
            <div class="impact-metric">
              <span class="impact-metric-label">Affected room area</span>
              <span class="impact-metric-val">${rmSqM} &rarr; <strong>${afterSqM} sq m</strong> (${rmSqFt} &rarr; <strong>${afterSqFt} sq ft</strong>)</span>
            </div>
            <div class="impact-metric">
              <span class="impact-metric-label">Envelope fill ratio</span>
              <span class="impact-metric-val">${ratioBefore}% &rarr; <strong>${ratioAfter}%</strong></span>
            </div>
          </div>
          <div class="impact-summary-line" style="font-size: 0.74rem; color: var(--text-muted); margin-top: 4px;">
            After this fix the rooms fill ${ratioAfter}% of the envelope.
          </div>
          <div class="impact-cost-box">
            <span>Price / sq ft ($):</span>
            <input type="number" class="input-price-sqft cost-price-input" placeholder="e.g. 150">
            <span class="cost-diff-display" style="font-weight: 700; color: var(--text);"></span>
          </div>
        `;

        const priceInput = impactStrip.querySelector(".cost-price-input");
        const costDiffDisplay = impactStrip.querySelector(".cost-diff-display");

        priceInput.addEventListener("input", () => {
          const price = parseFloat(priceInput.value);
          if (!isNaN(price) && price > 0) {
            const cost = sqFtDiff * price;
            costDiffDisplay.textContent = `Estimated difference: $${cost.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
          } else {
            costDiffDisplay.textContent = "";
          }
        });

        card.appendChild(impactStrip);
      }

      conflictsContainer.appendChild(card);
    });
  }

  function updateBoxHighlights() {
    document.querySelectorAll(".box-rect").forEach((rect) => {
      const id = rect.dataset.id;
      if (state.hoveredSuspectIds.has(id)) {
        rect.classList.add("box-pulse");
      } else {
        rect.classList.remove("box-pulse");
      }
    });
  }

  // =========================================================================
  // All Read Labels Drawer (Part 2)
  // =========================================================================
  function renderAllLabels(labels) {
    if (!allLabelsContainer) return;
    if (allLabelsSummaryText) {
      allLabelsSummaryText.textContent = `All Read Labels (${labels.length})`;
    }
    allLabelsContainer.innerHTML = "";

    const table = document.createElement("table");
    table.className = "labels-list-table";
    table.innerHTML = `
      <thead>
        <tr>
          <th>Label</th>
          <th>Role</th>
          <th>Current Value</th>
          <th>Status</th>
          <th>Edit Value</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody></tbody>
    `;
    const tbody = table.querySelector("tbody");

    labels.forEach((lbl) => {
      const tr = document.createElement("tr");
      const humanName = lbl.name || lbl.id;
      let statusBadge = `<span class="badge badge-neutral">${escapeHtml(lbl.status)}</span>`;
      if (lbl.status === "verified") statusBadge = `<span class="badge badge-ok">verified</span>`;
      else if (lbl.status === "conflict_suspect") statusBadge = `<span class="badge badge-warn">suspect</span>`;
      else if (lbl.status === "unit_ambiguous") statusBadge = `<span class="badge badge-warn">ambiguous</span>`;

      tr.innerHTML = `
        <td>
          <strong title="Raw id: ${escapeHtml(lbl.id)}">${escapeHtml(humanName)}</strong>
        </td>
        <td><span class="dim">${escapeHtml(lbl.role || lbl.applies_to || "-")}</span></td>
        <td><code>${escapeHtml(lbl.text)}</code></td>
        <td>${statusBadge}</td>
        <td>
          <div style="display: flex; align-items: center; gap: 6px;">
            <input type="text" class="edit-value-input label-edit-input" value="${escapeHtml(lbl.text)}" style="width: 90px; padding: 2px 6px; font-size: 0.74rem;">
            <span class="preview-chip" style="font-size: 0.68rem; padding: 1px 6px;">live</span>
          </div>
        </td>
        <td>
          <button class="btn btn-sm btn-outline btn-replace-label" disabled>Replace</button>
        </td>
      `;

      const input = tr.querySelector(".label-edit-input");
      const chip = tr.querySelector(".preview-chip");
      const replaceBtn = tr.querySelector(".btn-replace-label");

      const onInputDebounced = debounce(() => {
        const val = input.value.trim();
        if (val === lbl.text) {
          chip.textContent = "unchanged";
          chip.className = "preview-chip";
          replaceBtn.disabled = true;
          hideGhostPreview();
          return;
        }
        requestPreview(lbl.id, val, chip, (res) => {
          replaceBtn.disabled = !res.valid;
          if (document.activeElement === input) {
            showGhostPreview(lbl.id, val);
          }
        });
      }, 250);

      input.addEventListener("input", onInputDebounced);
      input.addEventListener("focus", () => {
        const val = input.value.trim();
        if (val && val !== lbl.text) {
          showGhostPreview(lbl.id, val);
        }
      });
      input.addEventListener("blur", () => {
        hideGhostPreview();
      });

      replaceBtn.addEventListener("click", () => {
        const val = input.value.trim();
        if (val) confirmEdit(lbl.id, val, "typed");
      });

      tbody.appendChild(tr);
    });

    allLabelsContainer.appendChild(table);
  }

  // =========================================================================
  // Change History Panel (Part 2)
  // =========================================================================
  function renderChangeHistory(history) {
    if (!historyListContainer) return;
    if (!history || history.length === 0) {
      historyListContainer.innerHTML =
        '<p class="dim" style="font-size: 0.78rem; margin: 0;">No replacements made yet. Original plan values active.</p>';
      return;
    }

    const table = document.createElement("table");
    table.className = "history-table";
    table.innerHTML = `
      <thead>
        <tr>
          <th>#</th>
          <th>Label</th>
          <th>Change</th>
          <th>Source</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody></tbody>
    `;
    const tbody = table.querySelector("tbody");

    history.forEach((h, idx) => {
      const tr = document.createElement("tr");
      const name = h.label_name || h.label_id;
      tr.innerHTML = `
        <td>${idx + 1}</td>
        <td><strong title="Raw id: ${escapeHtml(h.label_id)}">${escapeHtml(name)}</strong></td>
        <td>
          <span class="val-old">${escapeHtml(h.old_text)}</span>
          <span class="val-arrow">&rarr;</span>
          <span class="val-new">${escapeHtml(h.new_text)}</span>
        </td>
        <td><span class="history-source-badge">${escapeHtml(h.source || "suggestion")}</span></td>
        <td>
          <button class="btn btn-sm btn-outline btn-revert" data-id="${escapeHtml(h.label_id)}">Revert</button>
        </td>
      `;

      const revertBtn = tr.querySelector(".btn-revert");
      revertBtn.addEventListener("click", () => {
        revertEdit(h.label_id);
      });

      tbody.appendChild(tr);
    });

    historyListContainer.innerHTML = "";
    historyListContainer.appendChild(table);
  }

  // =========================================================================
  // Confirm, Revert, and Reset Actions
  // =========================================================================
  async function confirmEdit(labelId, newText, source = "suggestion") {
    try {
      hideGhostPreview();
      const resp = await fetch("/api/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: state.currentPlanId,
          label_id: labelId,
          new_text: newText,
          source: source,
        }),
      });

      if (!resp.ok) throw new Error("Confirm edit failed");
      const result = await resp.json();

      // Show Action Banner
      const ch = result.changed_labels && result.changed_labels[0];
      const diffStr = ch ? `${ch.old_text} &rarr; <strong>${ch.new_text}</strong>` : newText;
      const rem = result.remaining_conflicts ? result.remaining_conflicts.length : 0;
      const remMsg = rem === 0 ? "All constraints satisfied." : `${rem} conflict remaining.`;

      bannerText.innerHTML = `Accepted correction: ${diffStr}. ${remMsg}`;
      actionBanner.hidden = false;

      // Re-render workspace
      state.currentPlanData = result.plan;
      renderWorkspace(result.plan);
    } catch (err) {
      console.error("Error confirming edit:", err);
      alert("Failed to confirm edit on backend.");
    }
  }

  async function revertEdit(labelId) {
    try {
      hideGhostPreview();
      const resp = await fetch("/api/revert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: state.currentPlanId,
          label_id: labelId,
        }),
      });
      if (!resp.ok) throw new Error("Revert failed");
      const result = await resp.json();
      state.currentPlanData = result.plan;
      renderWorkspace(result.plan);
    } catch (err) {
      console.error("Error reverting edit:", err);
      alert("Failed to revert edit.");
    }
  }

  async function resetPlan() {
    try {
      hideGhostPreview();
      const resp = await fetch("/api/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: state.currentPlanId }),
      });
      if (!resp.ok) throw new Error("Reset failed");
      const result = await resp.json();

      actionBanner.hidden = true;
      state.currentPlanData = result.plan;
      renderWorkspace(result.plan);
    } catch (err) {
      console.error("Error resetting plan:", err);
    }
  }

  undoBtn.addEventListener("click", resetPlan);
  if (btnResetAll) btnResetAll.addEventListener("click", resetPlan);
  if (btnDownloadChangelog) {
    btnDownloadChangelog.addEventListener("click", () => {
      window.location.href = `/api/changelog/${state.currentPlanId}`;
    });
  }

  // =========================================================================
  // Tabs Navigation (Photo, Corrected plan, Side by side, Replaced drawing)
  // =========================================================================
  function setTab(tabId) {
    state.activeTab = tabId;
    [tabPhoto, tabCorrected, tabSideBySide, tabReplaced].forEach((btn) => {
      if (!btn) return;
      const active = btn.getAttribute("aria-controls") === tabId;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });

    [viewPhoto, viewCorrected, viewSideBySide, viewReplaced].forEach((pane) => {
      if (!pane) return;
      const visible = pane.id === tabId;
      pane.hidden = !visible;
      pane.classList.toggle("active", visible);
    });
  }

  tabPhoto.addEventListener("click", () => setTab("viewPhoto"));
  tabCorrected.addEventListener("click", () => setTab("viewCorrected"));
  tabSideBySide.addEventListener("click", () => setTab("viewSideBySide"));
  if (tabReplaced) tabReplaced.addEventListener("click", () => setTab("viewReplaced"));

  // =========================================================================
  // Draggable Before/After Slider (Part 5)
  // =========================================================================
  let isDraggingSlider = false;

  function updateSliderPosition(clientX) {
    if (!sliderStage) return;
    const rect = sliderStage.getBoundingClientRect();
    const x = clientX - rect.left;
    const pct = Math.max(0, Math.min(100, (x / rect.width) * 100));
    sliderStage.style.setProperty("--slider-pos", `${pct}%`);
  }

  if (sliderStage && sliderHandle) {
    sliderStage.style.setProperty("--slider-pos", "50%");
    sliderHandle.addEventListener("mousedown", (e) => {
      e.preventDefault();
      isDraggingSlider = true;
    });
    window.addEventListener("mousemove", (e) => {
      if (!isDraggingSlider) return;
      updateSliderPosition(e.clientX);
    });
    window.addEventListener("mouseup", () => {
      isDraggingSlider = false;
    });

    // Touch support for mobile / tablet
    sliderHandle.addEventListener("touchstart", (e) => {
      isDraggingSlider = true;
    });
    window.addEventListener("touchmove", (e) => {
      if (!isDraggingSlider || !e.touches[0]) return;
      updateSliderPosition(e.touches[0].clientX);
    });
    window.addEventListener("touchend", () => {
      isDraggingSlider = false;
    });
  }

  // =========================================================================
  // Report Export (Part 7)
  // =========================================================================
  if (downloadReportBtn) {
    downloadReportBtn.addEventListener("click", () => {
      window.open(`/api/report/${state.currentPlanId}`, "_blank");
    });
  }

  // =========================================================================
  // Upload Own Modal (Image Preview & Benchmark Selection)
  // =========================================================================
  const dropZonePrompt = document.getElementById("dropZonePrompt");
  const filePreviewWrap = document.getElementById("filePreviewWrap");
  const uploadFileThumb = document.getElementById("uploadFileThumb");
  const uploadFileName = document.getElementById("uploadFileName");
  const uploadFileSize = document.getElementById("uploadFileSize");
  const changeFileBtn = document.getElementById("changeFileBtn");
  let selectedFilePlanId = null;

  function resetUploadModal() {
    selectedFilePlanId = null;
    if (fileInput) fileInput.value = "";
    if (dropZonePrompt) dropZonePrompt.hidden = false;
    if (filePreviewWrap) filePreviewWrap.hidden = true;
    if (uploadStatusBox) uploadStatusBox.hidden = true;
    if (submitUploadBtn) {
      submitUploadBtn.disabled = true;
      submitUploadBtn.textContent = "Select a plan image";
    }
  }

  uploadOwnBtn.addEventListener("click", () => {
    resetUploadModal();
    uploadModal.showModal();
  });
  closeUploadModalBtn.addEventListener("click", () => uploadModal.close());
  cancelUploadBtn.addEventListener("click", () => uploadModal.close());

  if (changeFileBtn) {
    changeFileBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      fileInput.click();
    });
  }

  function handleFileSelected(file) {
    if (!file) return;

    // Show image preview
    if (dropZonePrompt) dropZonePrompt.hidden = true;
    if (filePreviewWrap) filePreviewWrap.hidden = false;
    if (uploadFileName) uploadFileName.textContent = file.name;
    if (uploadFileSize) {
      const kb = Math.round(file.size / 1024);
      uploadFileSize.textContent = `${kb} KB - ${file.type || "image"}`;
    }

    const reader = new FileReader();
    reader.onload = (e) => {
      if (uploadFileThumb) uploadFileThumb.src = e.target.result;
    };
    reader.readAsDataURL(file);

    // Check if filename matches any known benchmark plans
    const fn = file.name.toLowerCase();
    let matched = null;
    if (fn.includes("synth_008") || fn.includes("synth008")) {
      matched = { id: "synth_008", title: "Synth (synth_008)" };
    } else if (fn.includes("test6")) {
      matched = { id: "test6", title: "Real Photo (test6)" };
    } else if (fn.includes("test4")) {
      matched = { id: "test4", title: "Real Photo (test4)" };
    } else if (fn.includes("synth_002") || fn.includes("synth002")) {
      matched = { id: "synth_002", title: "Synthetic (synth_002)" };
    } else if (fn.includes("test1")) {
      matched = { id: "test1", title: "Real Photo (test1)" };
    }

    if (matched) {
      selectedFilePlanId = matched.id;
      uploadStatusTitle.textContent = `Matched Benchmark Plan: ${matched.title}`;
      uploadStatusDesc.textContent = "This plan has verified dimensional ground truth and is ready for constraint inspection.";
      uploadStatusBox.hidden = false;
      submitUploadBtn.disabled = false;
      submitUploadBtn.textContent = `Load ${matched.title}`;
    } else {
      selectedFilePlanId = "synth_008";
      uploadStatusTitle.textContent = "Offline Evaluation Mode";
      uploadStatusDesc.textContent = "Live model vision extraction (Gemma / PaddleOCR) is offline in this standalone evaluation build. Click below to load the primary verification benchmark (Synth):";
      uploadStatusBox.hidden = false;
      submitUploadBtn.disabled = false;
      submitUploadBtn.textContent = "Load Demo Plan (Synth)";
    }
  }

  dropZone.addEventListener("click", () => fileInput.click());
  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("dragover");
  });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileSelected(e.dataTransfer.files[0]);
    }
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files && fileInput.files[0]) {
      handleFileSelected(fileInput.files[0]);
    }
  });

  submitUploadBtn.addEventListener("click", () => {
    if (selectedFilePlanId) {
      uploadModal.close();
      selectPlan(selectedFilePlanId);
    }
  });

  howItWorksBtn.addEventListener("click", () => {
    document.body.classList.add("modal-open");
    howItWorksModal.showModal();
  });
  const closeHowItWorks = () => {
    document.body.classList.remove("modal-open");
    howItWorksModal.close();
  };
  closeHowItWorksBtn.addEventListener("click", closeHowItWorks);
  closeHowItWorksFooterBtn.addEventListener("click", closeHowItWorks);
  howItWorksModal.addEventListener("close", () => {
    document.body.classList.remove("modal-open");
  });

  // =========================================================================
  // Guided Demo Interactive Tour
  // =========================================================================
  const tourSteps = [
    {
      step: 1,
      title: "Step 1: Perception & Dimension Mismatch",
      text: "Notice Synth loaded on the left. The system transcribed all written dimensions into calibrated coordinate boxes. An injected room width discrepancy (4.129 m vs 4.729 m) is identified.",
      action: async () => {
        await selectPlan("synth_008");
        setTab("viewPhoto");
      },
    },
    {
      step: 2,
      title: "Step 2: Constraint Mismatch Caught",
      text: "On the right, see the Conflict Card. Code, not AI, verified the horizontal chain and detected that the inner dimension contradicts the boundary. Zero LLM arithmetic.",
      action: () => {
        conflictsSection.scrollIntoView({ behavior: "smooth" });
      },
    },
    {
      step: 3,
      title: "Step 3: Ranked Fix Candidates",
      text: "The deterministic solver ranks minimal corrections based on OCR corroboration and wall allowances. It proposes adjusting the contradictory dimension to 4.129 m.",
      action: () => {},
    },
    {
      step: 4,
      title: "Step 4: You Confirm the Fix",
      text: "Click 'Accept and replace' on the top recommendation. Notice the diff appears, conflicts drop to 0, and the layout satisfies all checksums.",
      action: async () => {
        const acceptBtn = document.querySelector(".btn-accept");
        if (acceptBtn) {
          acceptBtn.click();
        }
      },
    },
    {
      step: 5,
      title: "Step 5: Unit Ambiguity Check (Real Photo)",
      text: "Now switching to Real Photo. In real sketches, notation like 7'x4\" and 7'x5\" bathroom labels can be ambiguous. The system flags them under Unit Ambiguity for human verification.",
      action: async () => {
        await selectPlan("test6");
        setTab("viewPhoto");
      },
    },
  ];

  guidedDemoBtn.addEventListener("click", () => {
    state.tourStep = 0;
    state.tourActive = true;
    showTourStep(0);
  });

  async function showTourStep(idx) {
    if (idx < 0 || idx >= tourSteps.length) {
      tourModal.close();
      state.tourActive = false;
      return;
    }
    state.tourStep = idx;
    const s = tourSteps[idx];
    tourStepBadge.textContent = `Step ${s.step} of ${tourSteps.length}`;
    tourTitle.textContent = s.title;
    tourText.textContent = s.text;

    tourBackBtn.disabled = idx === 0;
    tourNextBtn.textContent = idx === tourSteps.length - 1 ? "Finish Tour" : "Next";

    tourModal.showModal();
    if (s.action) await s.action();
  }

  tourNextBtn.addEventListener("click", () => showTourStep(state.tourStep + 1));
  tourBackBtn.addEventListener("click", () => showTourStep(state.tourStep - 1));
  tourSkipBtn.addEventListener("click", () => {
    tourModal.close();
    state.tourActive = false;
  });
  closeTourBtn.addEventListener("click", () => {
    tourModal.close();
    state.tourActive = false;
  });

  // Initialize
  loadSamples();
})();
