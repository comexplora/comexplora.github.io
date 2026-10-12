/* Comexplora · núcleo de cálculo de las calculadoras de importación y exportación.
   Funciones puras, sin acceso al DOM: las usan assets/app.js (en el navegador) y tests/calc.test.js (Node).
   Las alícuotas, topes, fechas y fuentes vienen de data/calc-rules.json (no están escritas acá).
   Premisas del modelo (también se muestran en la página):
   - Importación definitiva para consumo por despacho general (no courier ni regímenes especiales).
   - Valor en aduana = valor CIF (precio + gastos hasta el embarque + flete + seguro, según el Incoterm).
   - Derecho de importación y tasa de estadística sobre el CIF; IVA y percepciones sobre CIF + derecho + tasa
     (art. 25 de la ley de IVA; RG 2937 art. 7; RG 2281 arts. 5 y 6). Ingresos Brutos (SIRPEI): se aproxima con la misma base.
   - Exportación: derecho sobre el FOB menos el CIF de los insumos importados temporariamente; reintegro sobre el FOB
     menos el CIF de todos los insumos importados incorporados y las comisiones (Decreto 571/96). */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ComexCalc = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* ---------------------------------------------------------------- números en formato argentino */
  // Acepta 1250 · 1250,50 · 1.250 · 1.250,50 · 1250.50 · 2,5 · 2.5. Rechaza lo ambiguo (1,250.50 o 1,250,000) y lo que no es número.
  function parseAmount(raw) {
    if (typeof raw === 'number') return Number.isFinite(raw) ? (raw < 0 ? { ok: false, error: 'No puede ser negativo.' } : { ok: true, value: raw }) : { ok: false, error: 'No es un número válido.' };
    let t = String(raw == null ? '' : raw).trim().replace(/\s+/g, '').replace(/^(US\$|U\$S|USD|ARS|\$)/i, '').replace(/%$/, '');
    if (t === '') return { ok: true, empty: true, value: null };
    let neg = false;
    if (/^[-−]/.test(t)) { neg = true; t = t.slice(1); }
    let n;
    if (/^[1-9]\d{0,2}(\.\d{3})+(,\d+)?$/.test(t)) n = t.replace(/\./g, '').replace(',', '.');      // 1.250 · 1.250,50 · 10.000
    else if (/^\d+,\d+$/.test(t)) n = t.replace(',', '.');                                             // 1250,50 · 2,5
    else if (/^\d+(\.\d+)?$/.test(t)) n = t;                                                          // 1250 · 1250.50 · 2.5
    else if (/^\d{1,3}(,\d{3})+(\.\d+)?$/.test(t)) return { ok: false, error: 'Formato ambiguo: usá punto para los miles y coma para los decimales (por ejemplo, 1.250,50).' };
    else return { ok: false, error: 'No es un número válido. Ejemplos: 1250, 1.250 o 1.250,50.' };
    const v = parseFloat(n);
    if (!Number.isFinite(v)) return { ok: false, error: 'No es un número válido.' };
    if (neg && v !== 0) return { ok: false, error: 'No puede ser negativo.' };
    return { ok: true, value: v };
  }

  // Lee y valida un campo. opts: { label, required, min, max, gt (mayor estricto que), def (valor si está vacío) }
  function reader(input, errors) {
    return function (key, opts) {
      opts = opts || {};
      const r = parseAmount(input[key]);
      if (!r.ok) { errors[key] = r.error; return null; }
      if (r.empty) {
        if (opts.required) { errors[key] = opts.requiredMsg || 'Completá este dato.'; return null; }
        return opts.def === undefined ? 0 : opts.def;
      }
      const v = r.value;
      if (opts.gt != null && !(v > opts.gt)) { errors[key] = opts.gtMsg || `Tiene que ser mayor que ${opts.gt}.`; return null; }
      if (opts.min != null && v < opts.min) { errors[key] = `No puede ser menor que ${opts.min}.`; return null; }
      if (opts.max != null && v > opts.max) { errors[key] = opts.maxMsg || `No puede ser mayor que ${opts.max}.`; return null; }
      return v;
    };
  }

  /* ---------------------------------------------------------------- reglas */
  const INCOTERMS = {
    EXW: { origen: true, flete: true, seguro: true },
    FCA: { origen: true, flete: true, seguro: true },
    FOB: { flete: true, seguro: true },
    CFR: { seguro: true },
    CIF: {},
  };
  const SITUACIONES = {
    ri: 'Responsable inscripto en IVA',
    mono: 'Monotributista',
    particular: 'Persona humana · uso o consumo particular',
  };

  function teCap(base, rules) {
    for (const [limit, cap] of rules.import.te.caps) if (limit == null || base <= limit) return cap;
    return null;
  }
  // Tasa de estadística: rate % del valor en aduana, con el tope del tramo (Decreto 1140/2024, art. 1).
  function teAmount(cif, rules) {
    const raw = cif * rules.import.te.rate / 100, cap = teCap(cif, rules);
    const capped = cap != null && raw > cap;
    return { amount: capped ? cap : raw, raw, cap, capped };
  }

  /* ---------------------------------------------------------------- importación */
  // Tratamiento de IVA y percepciones según la situación del importador y el destino del bien.
  function treatment(sit, destino, opts, rules, ivaRate) {
    const I = rules.import;
    const particular = sit === 'particular';
    const bienDeUso = !particular && destino === 'uso';
    const t = { notes: [] };
    // Percepción de IVA (RG 2937): excluidos el uso particular de personas humanas y los bienes de uso (art. 2).
    if (particular) { t.piva = 0; t.pivaWhy = 'Excluida: uso o consumo particular de una persona humana (RG 2937, art. 2).'; }
    else if (bienDeUso) { t.piva = 0; t.pivaWhy = 'Excluida: bien de uso para el importador (RG 2937, art. 2).'; }
    else if (opts.exclIva) { t.piva = 0; t.pivaWhy = 'Excluida por certificado de exclusión o por la exclusión temporaria de la RG 5490/5501.'; }
    else if (!ivaRate) { t.piva = 0; t.pivaWhy = 'Sin IVA, no hay percepción de IVA.'; }
    else { t.piva = I.percIva.rates[String(ivaRate)] || 0; t.pivaWhy = `${t.piva} % porque el IVA de la mercadería es ${String(ivaRate).replace('.', ',')} % (RG 2937, art. 7).`; }
    // Percepción de Ganancias (RG 2281): 6 % general, 11 % uso particular; excluidos los bienes de uso (art. 3).
    if (bienDeUso) { t.pgan = 0; t.pganWhy = 'Excluida: bien de uso para el importador (RG 2281, art. 3).'; }
    else if (opts.exclGan) { t.pgan = 0; t.pganWhy = 'Excluida por certificado de exclusión o por la exclusión temporaria de la RG 5490/5501.'; }
    else if (particular) { t.pgan = I.percGan.particular; t.pganWhy = `${t.pgan} %: bienes para uso o consumo particular del importador (RG 2281, art. 5).`; }
    else { t.pgan = I.percGan.general; t.pganWhy = `${t.pgan} %: alícuota general (RG 2281, art. 5).`; }
    // Ingresos Brutos (SIRPEI): alícuota que informa cada jurisdicción; un particular no inscripto no es agente pasible.
    if (particular) { t.piibb = 0; t.piibbWhy = 'No se aplica a una persona humana no inscripta en Ingresos Brutos.'; }
    else { t.piibb = opts.piibb; t.piibbWhy = 'Alícuota ingresada (cada provincia puede informar una alícuota propia para tu CUIT).'; }
    // Qué se recupera: solo un responsable inscripto computa el IVA como crédito fiscal y las percepciones a cuenta.
    if (sit === 'ri') {
      t.recIva = t.recPiva = t.recPgan = t.recPiibb = true;
      t.notes.push('El IVA es crédito fiscal y las percepciones son pagos a cuenta: se recuperan si tenés impuesto determinado contra el cual computarlos; si no, quedan como saldo a favor.');
    } else if (sit === 'mono') {
      t.recIva = t.recPiva = t.recPgan = t.recPiibb = false;
      t.notes.push('Un monotributista no computa crédito fiscal de IVA: el IVA y las percepciones se toman como costo. Confirmá con tu despachante qué percepciones te aplican y si podés pedir su devolución o cómputo.');
    } else {
      t.recIva = t.recPiva = t.recPiibb = false;
      t.recPgan = false;
      t.notes.push('Para un particular el IVA es costo. La percepción de Ganancias puede computarse como pago a cuenta si presentás declaración jurada; acá se toma como costo.');
    }
    return t;
  }

  function computeImport(input, rules, opts) {
    opts = opts || {};
    const today = opts.today || new Date().toISOString().slice(0, 10);
    const I = rules.import;
    const errors = {}, warnings = [], assumptions = [];
    const get = reader(input, errors);
    const inco = INCOTERMS[input.inco] ? input.inco : 'FOB';
    const inc = INCOTERMS[inco];
    const sit = SITUACIONES[input.situacion] ? input.situacion : 'ri';
    const destino = input.destino === 'uso' ? 'uso' : 'cambio';
    const origin = ['extra', 'mercosur', 'acuerdo'].includes(input.origin) ? input.origin : 'extra';
    const ivaRate = I.ivaRates.includes(Number(input.iva)) ? Number(input.iva) : 21;

    const precio = get('precio', { required: true, gt: 0, gtMsg: 'Ingresá un precio mayor que cero.', requiredMsg: 'Ingresá el precio de compra.' });
    const origen = inc.origen ? get('origen', { min: 0 }) : 0;
    const flete = inc.flete ? get('flete', { min: 0 }) : 0;
    const seguro = inc.seguro ? get('seguro', { min: 0 }) : 0;
    const tc = get('tc', { def: null, gt: 0, gtMsg: 'El tipo de cambio tiene que ser mayor que cero.' });
    // Derecho de importación: se exige el dato salvo origen Mercosur con preferencia total.
    const diForcedZero = origin === 'mercosur' && !input.mercoExcluded;
    const di = diForcedZero ? 0 : get('di', { required: true, min: 0, max: 100, requiredMsg: 'Ingresá la alícuota del derecho de importación de tu posición arancelaria (NCM).', maxMsg: 'El derecho de importación no puede superar el 100 %.' });
    const piibbIn = get('piibb', { def: 0, min: 0, max: 15, maxMsg: 'Revisá la alícuota: la general del SIRPEI es 2,5 %.' });
    const gastosList = [
      ['despachante', 'Despachante de aduana'],
      ['terminal', 'Terminal, depósito y gastos portuarios'],
      ['fleteint', 'Flete interno'],
      ['otros', 'Otros gastos'],
    ].map(([k, label]) => [label, get(k, { min: 0 })]);

    if (Object.keys(errors).length) return { ok: false, errors, warnings, assumptions, missing: Object.keys(errors) };

    // 1. Valor en aduana (CIF)
    const fob = inc.flete ? precio + origen : null;
    const cif = precio + origen + flete + seguro;

    // 2. Tasa de estadística
    let teExemptWhy = '';
    if (origin === 'mercosur') teExemptWhy = 'Exenta: mercadería originaria del Mercosur (Decreto 1140/2024, art. 2).';
    else if (origin === 'acuerdo' && input.acuerdoTeExento) teExemptWhy = 'Exenta: el acuerdo preferencial prevé la exención (Decreto 1140/2024, art. 2).';
    else if (input.teExentaEspecial) teExemptWhy = 'Exenta por una norma especial indicada por vos.';
    const te = teExemptWhy ? { amount: 0, raw: 0, cap: null, capped: false } : teAmount(cif, rules);
    if (!teExemptWhy && today > I.te.validUntil)
      warnings.push(`La alícuota del ${I.te.rate} % de la tasa de estadística rige hasta el ${I.te.validUntil.split('-').reverse().join('/')} (${I.te.norm}). Verificá si fue prorrogada o modificada.`);

    // 3. Derecho de importación
    const diAmount = cif * di / 100;
    if (di > 35) warnings.push('El derecho de importación ingresado supera el 35 %, el máximo habitual del Arancel Externo Común. Verificá la alícuota de tu posición.');
    if (origin === 'mercosur') {
      warnings.push('Origen Mercosur: la preferencia exige que la mercadería cumpla el régimen de origen y que se presente un certificado de origen válido. Sin él se paga el derecho extrazona y la tasa de estadística.');
      if (input.mercoExcluded) assumptions.push('Producto excluido del libre comercio intra-Mercosur (por ejemplo, azúcar o sector automotor): se usa el derecho de importación que ingresaste.');
    }
    if (origin === 'acuerdo') assumptions.push('Acuerdo preferencial: el derecho ingresado debe ser el que resulta después de aplicar la preferencia.');

    // 4. Base imponible, IVA y percepciones
    const base = cif + diAmount + te.amount;
    const tr = treatment(sit, destino, { exclIva: !!input.exclIva, exclGan: !!input.exclGan, piibb: piibbIn }, rules, ivaRate);
    const iva = base * ivaRate / 100;
    const piva = base * tr.piva / 100;
    const pgan = base * tr.pgan / 100;
    const piibb = base * tr.piibb / 100;
    const gastos = gastosList.reduce((s, [, v]) => s + v, 0);
    const tributos = diAmount + te.amount + iva + piva + pgan + piibb;
    const recuperable = (tr.recIva ? iva : 0) + (tr.recPiva ? piva : 0) + (tr.recPgan ? pgan : 0) + (tr.recPiibb ? piibb : 0);
    const desembolso = cif + tributos + gastos;
    const costo = desembolso - recuperable;

    if (inc.seguro && seguro === 0) warnings.push('El seguro está en 0. Si no contratás seguro, consultá con tu despachante: el valor en aduana puede incluir un seguro presunto.');
    if (te.capped) assumptions.push(`La tasa de estadística llegó al tope de USD ${te.cap.toLocaleString('es-AR')} que fija el ${I.te.norm} para este tramo de valor en aduana.`);
    if (piibbIn > 5) warnings.push('La alícuota de Ingresos Brutos ingresada es alta: la general del SIRPEI es 2,5 %. Verificala en tu padrón.');
    if (today > I.exclusions.until && (input.exclIva || input.exclGan))
      warnings.push(`La exclusión temporaria de la RG 5490/5501 regía hasta el ${I.exclusions.until.split('-').reverse().join('/')}. Verificá si sigue vigente.`);

    const res = {
      ok: true, errors, warnings, assumptions, notes: tr.notes,
      inco, sit, destino, origin, ivaRate,
      precio, origen, flete, seguro, fob, cif, tc,
      di, diAmount, te, teExemptWhy, base,
      iva, piva, pgan, piibb,
      rates: { piva: tr.piva, pgan: tr.pgan, piibb: tr.piibb },
      why: { piva: tr.pivaWhy, pgan: tr.pganWhy, piibb: tr.piibbWhy },
      rec: { iva: tr.recIva, piva: tr.recPiva, pgan: tr.recPgan, piibb: tr.recPiibb },
      percepciones: piva + pgan + piibb,
      tributos, recuperable, gastos, gastosList, desembolso, costo,
    };
    // Escenarios: mismo caso con otras situaciones fiscales (para quien no sabe cuál le corresponde o quiere comparar).
    if (!opts.noScenarios) {
      res.scenarios = [['ri', 'cambio'], ['ri', 'uso'], ['mono', 'cambio'], ['particular', 'cambio']].map(([s, d]) => {
        const r = computeImport(Object.assign({}, input, { situacion: s, destino: d }), rules, { today, noScenarios: true });
        return { sit: s, destino: d, label: SITUACIONES[s] + (s === 'particular' ? '' : d === 'uso' ? ' · bien de uso' : ' · reventa o insumo'),
                 desembolso: r.desembolso, costo: r.costo, recuperable: r.recuperable, current: s === sit && (s === 'particular' || d === destino) };
      });
    }
    return res;
  }

  /* ---------------------------------------------------------------- exportación */
  function computeExport(input, rules) {
    const errors = {}, warnings = [], assumptions = [];
    const get = reader(input, errors);
    const regimen = input.regimen === 'temporaria' ? 'temporaria' : 'ninguno';
    const reintEleg = ['si', 'no', 'nose'].includes(input.reintEleg) ? input.reintEleg : 'no';
    const fob = get('fob', { required: true, gt: 0, requiredMsg: 'Ingresá el valor FOB.', gtMsg: 'Ingresá un valor FOB mayor que cero.' });
    const dex = get('dex', { required: true, min: 0, max: 100, requiredMsg: 'Ingresá la alícuota del derecho de exportación de tu posición (puede ser 0 %).' });
    const insTemp = regimen === 'temporaria' ? get('insTemp', { min: 0 }) : 0;
    const insOtros = get('insOtros', { min: 0 });
    const comis = get('comis', { min: 0 });
    const reint = reintEleg === 'no' ? 0 : get('reint', { required: true, min: 0, max: 20, requiredMsg: 'Ingresá la alícuota de reintegro de tu posición.', maxMsg: 'Revisá la alícuota: los reintegros no superan el 20 %.' });
    const tc = get('tc', { def: null, gt: 0, gtMsg: 'El tipo de cambio tiene que ser mayor que cero.' });
    const gastosList = [
      ['despachante', 'Despachante de aduana'],
      ['terminal', 'Terminal, depósito y gastos portuarios'],
      ['fleteint', 'Flete interno hasta el puerto'],
      ['otros', 'Otros gastos'],
    ].map(([k, label]) => [label, get(k, { min: 0 })]);
    if (fob != null) {
      if (insTemp != null && insTemp > fob) errors.insTemp = 'Los insumos importados no pueden superar el valor FOB.';
      if (insTemp != null && insOtros != null && insTemp + insOtros > fob && !errors.insTemp) errors.insOtros = 'La suma de insumos importados no puede superar el valor FOB.';
      if (comis != null && comis > fob) errors.comis = 'Las comisiones no pueden superar el valor FOB.';
    }
    if (Object.keys(errors).length) return { ok: false, errors, warnings, assumptions, missing: Object.keys(errors) };

    const baseDex = fob - insTemp;
    const dexAmount = baseDex * dex / 100;
    const baseReint = Math.max(0, fob - insTemp - insOtros - comis);
    const reintAmount = baseReint * reint / 100;
    const reintApplied = reintEleg === 'si' ? reintAmount : 0;
    const gastos = gastosList.reduce((s, [, v]) => s + v, 0);
    const neto = fob - dexAmount - comis - gastos + reintApplied;

    if (regimen === 'temporaria') assumptions.push('Se descuenta de la base del derecho el valor CIF de los insumos importados temporariamente (Decreto 1330/2004 y arts. 735 a 737 del Código Aduanero). El factor de ponderación y la cancelación de la destinación temporaria los define tu despachante.');
    else assumptions.push('Sin importación temporaria: el derecho de exportación se calcula sobre todo el valor FOB. Los insumos importados en forma definitiva no se descuentan de esa base.');
    if (reintEleg === 'nose') warnings.push(`No confirmaste que la posición tenga reintegro: no se suma al ingreso neto. Si corresponde, serían USD ${reintAmount.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}. Verificá la alícuota y los requisitos con tu despachante.`);
    if (reintEleg === 'si') assumptions.push('El reintegro se calcula sobre el FOB menos el CIF de los insumos importados incorporados y las comisiones (Decreto 571/1996) y se cobra en pesos después del embarque.');
    if (dex > 0 && dex > 33) warnings.push('El derecho de exportación ingresado es muy alto. Verificá la alícuota vigente de tu posición.');

    return { ok: true, errors, warnings, assumptions, regimen, reintEleg, fob, dex, insTemp, insOtros, comis, baseDex, dexAmount,
             reint, baseReint, reintAmount, reintApplied, gastos, gastosList, neto, tc };
  }

  return { parseAmount, teAmount, teCap, computeImport, computeExport, INCOTERMS, SITUACIONES };
});
