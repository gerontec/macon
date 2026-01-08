(* ========================================================================== *)
(* FIX FÜR SOLAR-SENSOR MODBUS MAPPING                                       *)
(* ========================================================================== *)
(* 
   PROBLEM: Solar-Werte werden berechnet (temp_solar, S_Solar) aber NICHT
            ins xMeasure Array geschrieben
   
   LÖSUNG:  Fügen Sie folgende Zeilen im Abschnitt "9. MODBUS MAPPING" hinzu
*)

(* 9. MODBUS MAPPING (MW32 - MW167) - KORRIGIERT *)
status_word := 0;
IF NOT O1_WWPump THEN status_word := status_word + 1; END_IF;
IF NOT O2_UmwaelzHK1 THEN status_word := status_word + 2; END_IF;
IF O3_Brunnen THEN status_word := status_word + 4; END_IF;
IF bNachtAbsenkung THEN status_word := status_word + 8; END_IF;
IF mux_state THEN status_word := status_word + 16; END_IF;
IF bDataReady THEN status_word := status_word + 32; END_IF;

xMeasure[1] := WORD_TO_INT(S_Vorlauf); 
xMeasure[2] := WORD_TO_INT(S_Aussen);
xMeasure[3] := WORD_TO_INT(S_Innen); 
xMeasure[4] := WORD_TO_INT(S_Kessel);
xMeasure[5] := WORD_TO_INT(S_Warmw); 
xMeasure[6] := WORD_TO_INT(S_Oeltank);        (* ← NEU: Öltank Raw *)
xMeasure[7] := WORD_TO_INT(S_Ruecklauf);
xMeasure[8] := WORD_TO_INT(S_Solar);          (* ← NEU: Solar Raw *)
xMeasure[10] := hour_of_day; 
xMeasure[11] := status_word;
xMeasure[12] := REAL_TO_INT(temp_diff_ww * 100.0);
xMeasure[13] := REAL_TO_INT(temp_kessel * 100.0);
xMeasure[14] := REAL_TO_INT(temp_warmw * 100.0);
xMeasure[15] := REAL_TO_INT(temp_vorlauf * 100.0);
xMeasure[16] := BYTE_TO_INT(VERSION_MAJOR) * 256 + BYTE_TO_INT(VERSION_MINOR);
xMeasure[17] := BYTE_TO_INT(VERSION_PATCH);
xMeasure[19] := UDINT_TO_INT(LIMIT(0, WW_Runtime, 32767));
xMeasure[20] := UDINT_TO_INT(LIMIT(0, HK_Runtime, 32767));
xMeasure[21] := UDINT_TO_INT(LIMIT(0, BR_Runtime, 32767));
xMeasure[25] := BYTE_TO_INT(bWW_Reason);
xMeasure[26] := BYTE_TO_INT(bHK_Reason);
xMeasure[27] := BYTE_TO_INT(bBR_Reason);      (* ← Optional: BR Reason *)
xMeasure[28] := REAL_TO_INT(temp_aussen * 100.0);
xMeasure[29] := REAL_TO_INT(temp_innen * 100.0);   (* ← Optional: Innen-Temp *)
xMeasure[30] := REAL_TO_INT(temp_ruecklauf * 100.0);
xMeasure[31] := REAL_TO_INT(temp_solar * 100.0);   (* ← NEU: Solar berechnet *)
xMeasure[32] := BYTE_TO_INT(%QB0);

xSystem[1] := UDINT_TO_INT(uptime_seconds MOD 65536);
xSystem[2] := UDINT_TO_INT(uptime_seconds / 65536);
xStats[1] := UDINT_TO_INT(HK_Runtime / 3600);
xStats[2] := UDINT_TO_INT(WW_Runtime / 3600);
xStats[3] := UDINT_TO_INT(BR_Runtime / 3600);

(* ========================================================================== *)
(* ÄNDERUNGEN ZUSAMMENGEFASST:                                                *)
(* ========================================================================== *)
(* 
   HINZUGEFÜGT:
   - xMeasure[6]  = S_Oeltank (Raw)
   - xMeasure[8]  = S_Solar (Raw)              ← WICHTIG für Python!
   - xMeasure[27] = bBR_Reason
   - xMeasure[29] = temp_innen × 100
   - xMeasure[31] = temp_solar × 100           ← WICHTIG für Python!
   
   NACH DEM UPDATE:
   - Python heizung2.py wird Solar-Werte korrekt lesen
   - Python heizung3.py wird Solar-Werte korrekt lesen
   - Keine Änderungen an Python-Code nötig!
*)
