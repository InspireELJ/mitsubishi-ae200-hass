/* Run once in a modern browser Console on the controller's own HTTP page.
 * No Java plug-in, login attempt, configuration write or control command.
 * Only documented page GETs and known MELANS getRequest packets are sent.
 * If automatic discovery fails, fill GROUPS with already known group numbers.
 */
(async () => {
  "use strict";
  const GROUPS = [];
  const ATTRS = [
    "Drive", "Mode", "ModeStatus", "SetTemp", "SetTemp1", "SetTemp2", "SetTemp3", "SetTemp4", "SetTemp5",
    "SetHumidity", "InletTemp", "InletHumidity", "RoomHumidity", "AirDirection", "FanSpeed", "RemoCon",
    "DriveItem", "ModeItem", "SetTempItem", "FilterItem", "AirDirItem", "FanSpeedItem", "FilterSign", "ErrorSign",
    "Hold", "CheckWater", "Ventilation", "VentiDrive", "VentiFan", "VentMode", "Vent24h", "Vent24hMode",
    "Humid", "TempLimit", "CoolMin", "CoolMax", "HeatMin", "HeatMax", "AutoMin", "AutoMax", "OutdoorTemp",
    "InletTempHWHP", "OutletTempHWHP", "Occupancy", "Schedule", "ScheduleAvail",
  ];
  if (window.__ae200ReadProbe) {
    console.warn("A read-only probe is already running. Wait for its export.");
    return;
  }
  if (!/^https?:$/.test(location.protocol)) throw new Error("Run on the controller web page.");
  window.__ae200ReadProbe = true;
  const result = { schema: "inspire-ae200-readonly-probe-v1", captured_at: new Date().toISOString(), controller_origin: location.origin, pages: [], groups: {}, errors: [] };
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  let lastRequest = 0;
  const pace = async () => {
    await sleep(Math.max(0, 1100 - (Date.now() - lastRequest)));
    lastRequest = Date.now();
  };
  const parse = xml => {
    if (/<!DOCTYPE|<!ENTITY/i.test(xml)) throw new Error("Unsupported XML declaration");
    const doc = new DOMParser().parseFromString(xml, "application/xml");
    if (doc.getElementsByTagName("parsererror").length || doc.documentElement?.localName !== "Packet") throw new Error("Controller did not return a MELANS XML packet");
    const command = doc.getElementsByTagName("Command")[0]?.textContent;
    const err = doc.getElementsByTagName("ERROR")[0];
    if (err || command?.endsWith("ErrorResponse")) throw new Error(`Controller read rejected: ${err?.getAttribute("Code") || command}`);
    if (command !== "getResponse") throw new Error("Expected getResponse");
    return doc;
  };
  const read = async payload => {
    const command = new DOMParser().parseFromString(payload, "application/xml").getElementsByTagName("Command")[0]?.textContent;
    if (command !== "getRequest") throw new Error("Only getRequest is permitted");
    await pace();
    return await new Promise((resolve, reject) => {
      const ws = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/b_xmlproc/`, "b_xmlproc");
      let finished = false;
      const finish = (err, data) => {
        if (finished) return;
        finished = true; clearTimeout(timer); ws.close();
        if (err) reject(err); else resolve(data);
      };
      const timer = setTimeout(() => finish(new Error("WebSocket read timed out")), 10000);
      ws.onopen = () => ws.send(payload);
      ws.onerror = () => finish(new Error("WebSocket connection failed"));
      ws.onclose = () => { if (!finished) finish(new Error("WebSocket closed before a response")); };
      ws.onmessage = async event => {
        try {
          const text = typeof event.data === "string" ? event.data : await event.data.text();
          if (text.length > 2 * 1024 * 1024) throw new Error("Response too large");
          finish(null, parse(text));
        } catch (err) { finish(err); }
      };
    });
  };
  const wrap = body => `<?xml version="1.0" encoding="UTF-8"?><Packet><Command>getRequest</Command><DatabaseManager>${body}</DatabaseManager></Packet>`;
  try {
    // Endpoint checks collect no response bodies or login credentials.
    for (const path of ["/control/index.html", "/mobile/index.html", "/init/en/administrator.html"]) {
      await pace();
      try {
        const response = await fetch(path, { method: "GET", credentials: "omit", redirect: "error", signal: AbortSignal.timeout(10000) });
        const text = (await response.text()).slice(0, 512 * 1024);
        result.pages.push({ path, status: response.status, content_type: response.headers.get("content-type"), has_java_markup: /<applet\b|application\/x-java|java\.class/i.test(text) });
      } catch { result.pages.push({ path, error: "page_unavailable_or_redirected" }); }
    }
    let ids = [...new Set(GROUPS.map(String))];
    if (!ids.length) {
      const doc = await read(wrap("<ControlGroup><MnetList /></ControlGroup>"));
      ids = [...doc.getElementsByTagName("MnetRecord")].map(el => el.getAttribute("Group"));
    }
    ids = [...new Set(ids)].filter(id => /^\d+$/.test(id) && +id >= 1 && +id <= 50).slice(0, 50);
    result.configured_groups = ids;
    if (!ids.length) throw new Error("No configured groups returned. Use only already known GROUPS, then rerun.");
    let failures = 0;
    for (const gid of ids) {
      try {
        const doc = await read(wrap(`<Mnet Group="${gid}" ${ATTRS.map(a => `${a}="*"`).join(" ")} />`));
        const node = [...doc.getElementsByTagName("Mnet")].find(el => el.getAttribute("Group") === gid);
        if (!node) throw new Error("Requested group missing from response");
        const raw = Object.fromEntries([...node.attributes].filter(a => a.name !== "Group").map(a => [a.name, /password|token|secret|user|groupname|serial|licen[cs]e/i.test(a.name) ? "[REDACTED]" : a.value]));
        result.groups[gid] = { sampled_at: new Date().toISOString(), raw_attributes: raw };
        failures = 0;
      } catch (err) {
        result.errors.push({ group: gid, error: String(err?.message || err) });
        if (++failures >= 2) { result.errors.push({ error: "Stopped after two consecutive failed reads" }); break; }
      }
    }
  } catch (err) {
    result.errors.push({ error: String(err?.message || err) });
  } finally {
    window.__ae200ReadProbe = false;
    result.notes = [
      "No login attempts, state writes, guessed fields, full group scan or licence changes.",
      "Page status 200 alone does not prove a working modern UI.",
      "Raw fields are evidence; missing or zero fields do not prove feature support.",
      "The export contains the controller address; keep it internal.",
    ];
    const filename = `AE200_ReadOnly_${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob), a = document.createElement("a");
    a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    console.info("AE200 read-only probe exported", { filename, groups: Object.keys(result.groups).length, errors: result.errors });
  }
})();
