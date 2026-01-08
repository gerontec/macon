(* ========================================================================== *)
(* PLC_PRG - HAUPTPROGRAMM v6.5.4 - FIXED SOLAR MAPPING                      *)
(* ========================================================================== *)
(* ÄNDERUNGEN v6.5.4:
   - FIXED: Solar-Werte werden jetzt korrekt ins xMeasure Array geschrieben
   - ADDED: xMeasure[6] = S_Oeltank
   - ADDED: xMeasure[8] = S_Solar (Raw)
   - ADDED: xMeasure[27] = bBR_Reason
   - ADDED: xMeasure[29] = temp_innen × 100
   - ADDED: xMeasure[31] = temp_solar × 100 (berechnet)
*)

(* 1. INITIALISIERUNG *)
bWW_Reason := 0;
bHK_Reason := 0;
bBR_Reason := 0;
xAlarms[1] := 0;

(* 2. ECHTZEITUHR & NACHTABSENKUNG *)
rtc_dt := SysRtcGetTime(TRUE);
hour_of_day := DWORD_TO_INT((DT_TO_DWORD(rtc_dt) / 3600) MOD 24);

IF (xSetpoints[5] < 0) OR (xSetpoints[5] > 23) OR (xSetpoints[6] < 0) OR (xSetpoints[6] > 23) THEN
    bNachtAbsenkung := FALSE;
ELSIF (xSetpoints[5] = 0) AND (xSetpoints[6] = 0) THEN
    bNachtAbsenkung := FALSE;
ELSE
    nacht_start_hour := xSetpoints[5];
    nacht_end_hour := xSetpoints[6];
    IF nacht_start_hour < nacht_end_hour THEN
        bNachtAbsenkung := (hour_of_day >= nacht_start_hour) AND (hour_of_day < nacht_end_hour);
    ELSE
        bNachtAbsenkung := (hour_of_day >= nacht_start_hour) OR (hour_of_day < nacht_end_hour);
    END_IF;
END_IF;

(* 3. MULTIPLEXER (Phase A: 51s, Phase B: 8s) *)
IF mux_state THEN
    Timer_Long(IN := TRUE, PT := T#51S);
    IF Timer_Long.Q THEN
        mux_state := FALSE;
        Timer_Long(IN := FALSE);
        Timer_Settle(IN := FALSE);
    END_IF;
ELSE
    Timer_Short(IN := TRUE, PT := T#8S);
    IF Timer_Short.Q THEN
        mux_state := TRUE;
        Timer_Short(IN := FALSE);
        Timer_Settle(IN := FALSE);
    END_IF;
END_IF;

Output_0 := mux_state;
Timer_Settle(IN := TRUE, PT := T#2S);
bDataReady := Timer_Settle.Q;

(* 4. SAMPLE & HOLD *)
IF bDataReady THEN
    IF mux_state THEN
        S_Vorlauf := analog0; 
        S_Aussen := analog1; 
        S_Innen := analog2; 
        S_Kessel := analog3;
    ELSE
        S_Warmw := analog0; 
        S_Oeltank := analog1; 
        S_Ruecklauf := analog2; 
        S_Solar := analog3;
    END_IF;
END_IF;

(* 5. TEMPERATUR-BERECHNUNGEN (PT1000 & NTC) *)

(* 5.1 PT1000 Sensoren - Vorlauf *)
U_PT := (WORD_TO_REAL(S_Vorlauf) / Raw_Max) * U_SPS;
ohm_vorlauf := (U_PT * R_Mess) / (U_Vers - U_PT);
temp_vorlauf := TEMP_PT(ohm_vorlauf, PT1000_R0);

(* 5.2 PT1000 Sensoren - Kessel *)
U_PT := (WORD_TO_REAL(S_Kessel) / Raw_Max) * U_SPS;
ohm_kessel := (U_PT * R_Mess) / (U_Vers - U_PT);
temp_kessel := TEMP_PT(ohm_kessel, PT1000_R0);

(* 5.3 PT1000 Sensoren - Aussen *)
U_PT := (WORD_TO_REAL(S_Aussen) / Raw_Max) * U_SPS;
ohm_aussen := (U_PT * R_Mess) / (U_Vers - U_PT);
temp_aussen := TEMP_PT(ohm_aussen, PT1000_R0);

(* 5.4 PT1000 Sensoren - Innen *)
U_PT := (WORD_TO_REAL(S_Innen) / Raw_Max) * U_SPS;
ohm_innen := (U_PT * R_Mess) / (U_Vers - U_PT);
temp_innen := TEMP_PT(ohm_innen, PT1000_R0);

(* 5.5 PT1000 Sensoren - Ruecklauf *)
U_PT := (WORD_TO_REAL(S_Ruecklauf) / Raw_Max) * U_SPS;
ohm_ruecklauf := (U_PT * R_Mess) / (U_Vers - U_PT);
temp_ruecklauf := TEMP_PT(ohm_ruecklauf, PT1000_R0);

(* 5.6 NTC Sensoren - Boiler (Warmwasser) *)
raw_warmw_real := WORD_TO_REAL(S_Warmw);
IF (raw_warmw_real >= 4000.0) THEN 
    temp_warmw := (NTC_BOILER_A - raw_warmw_real) / NTC_BOILER_B; 
ELSE 
    temp_warmw := 0.0; 
END_IF;

(* 5.7 NTC Sensoren - Solar *)
raw_solar_real := WORD_TO_REAL(S_Solar);
IF (raw_solar_real >= 4000.0) THEN 
    temp_solar := (raw_solar_real - NTC_SOLAR_A) / NTC_SOLAR_B; 
ELSE 
    temp_solar := 0.0; 
END_IF;

(* 5.8 Temperaturdifferenz Kessel-Warmwasser *)
temp_diff_ww := temp_kessel - temp_warmw;

(* ========================================================================== *)
(* 6. HEIZKURVE (OSCAT HEAT_TEMP) - BERECHNUNG DES SOLLWERTS                *)
(* ========================================================================== *)
oscat_heat_curve(
    T_EXT  := temp_aussen,
    T_INT  := temp_innen,
    OFFSET := 0.0,
    T_REQ  := heat_curve_T_INT,
    TY_MAX := heat_curve_TY_MAX,
    TY_MIN := heat_curve_TY_MIN,
    C      := heat_curve_C
);

(* Ergebnis der Heizkurve in xSetpoints[1] schreiben *)
xSetpoints[1] := REAL_TO_INT(oscat_heat_curve.TY * 100.0);

(* Initialisierung falls noch nicht gesetzt *)
IF xSetpoints[1] <= 0 THEN
    xSetpoints[1] := REAL_TO_INT(oscat_heat_curve.TY * 100.0);
END_IF;
IF xSetpoints[10] = 0 THEN
    xSetpoints[10] := -500; (* -5.0°C Frostschutz *)
END_IF;

(* ========================================================================== *)
(* 7. PUMPENSTEUERUNG                                                         *)
(* ========================================================================== *)

(* 7.1 Warmwasser-Pumpe *)
IF temp_diff_ww >= 2.0 THEN 
    bWW_Reason := 16#01; 
END_IF;

IF xSetpoints[14] < 0 THEN
    bWW_Enable := FALSE; 
    bWW_Manual := TRUE;
ELSIF xSetpoints[14] > 0 THEN
    bWW_Enable := TRUE; 
    bWW_Manual := TRUE; 
    bWW_Reason := 16#80;
ELSE
    bWW_Enable := (bWW_Reason > 0); 
    bWW_Manual := FALSE;
END_IF;

ACTUATOR_WW(
    IN := bWW_Enable, 
    MANUAL := bWW_Manual, 
    MIN_ONTIME := T#120S, 
    RUNTIME := WW_Runtime, 
    CYCLES := WW_Cycles
);
O1_WWPump := NOT ACTUATOR_WW.PUMP;

(* 7.2 Heizkreis-Pumpe - VEREINFACHTE LOGIK *)
IF xSetpoints[15] = -1 THEN
    (* Python forciert AUS *)
    bHK_Enable := FALSE;
    bHK_Manual := TRUE;
    bHK_Reason := 0;
ELSIF xSetpoints[15] = 1 THEN
    (* Python forciert EIN *)
    bHK_Enable := TRUE;
    bHK_Manual := TRUE;
    bHK_Reason := 16#80;
ELSE  (* xSetpoints[15] = 0 *)
    (* Automatikbetrieb - Heizkurve *)
    bHK_Manual := FALSE;
    
    (* Vorlauf unter Sollwert? -> Pumpe EIN *)
    IF temp_vorlauf < (INT_TO_REAL(xSetpoints[1]) / 100.0) THEN
        bHK_Reason := HK_REASON_WAERMEBEDARF;
        bHK_Enable := TRUE;
    ELSE
        (* Sollwert erreicht -> Pumpe AUS *)
        bHK_Reason := 0;
        bHK_Enable := FALSE;
    END_IF;
END_IF;

ACTUATOR_HK(
    IN := bHK_Enable,
    MANUAL := bHK_Manual,
    MIN_ONTIME := T#5M,
    RUNTIME := HK_Runtime,
    CYCLES := HK_Cycles
);
O2_UmwaelzHK1 := NOT ACTUATOR_HK.PUMP;

(* 7.3 Brunnenpumpe - DEFAULT IMMER EIN *)
IF xSetpoints[16] = -1 THEN
    (* Python forciert AUS *)
    O3_Brunnen := FALSE; 
    bBR_Reason := 0;
ELSIF xSetpoints[16] = 1 THEN
    (* Python forciert EIN *)
    O3_Brunnen := TRUE; 
    bBR_Reason := 16#80;
ELSE  (* xSetpoints[16] = 0 *)
    (* Automatikbetrieb: Brunnen läuft IMMER *)
    O3_Brunnen := TRUE;      
    bBR_Reason := BR_REASON_AUTO_ON;
END_IF;

ACTUATOR_BR(
    IN := O3_Brunnen, 
    RUNTIME := BR_Runtime, 
    CYCLES := BR_Cycles
);

(* ========================================================================== *)
(* 8. UPTIME & TICK                                                           *)
(* ========================================================================== *)
Timer_OneSecond(IN := NOT Timer_OneSecond.Q, PT := T#1s);
IF Timer_OneSecond.Q THEN 
    uptime_seconds := uptime_seconds + 1; 
END_IF;

(* ========================================================================== *)
(* 9. MODBUS MAPPING (MW32 - MW167) - FIXED FOR SOLAR                        *)
(* ========================================================================== *)

(* 9.1 Status-Word zusammenbauen *)
status_word := 0;
IF NOT O1_WWPump THEN status_word := status_word + 1; END_IF;
IF NOT O2_UmwaelzHK1 THEN status_word := status_word + 2; END_IF;
IF O3_Brunnen THEN status_word := status_word + 4; END_IF;
IF bNachtAbsenkung THEN status_word := status_word + 8; END_IF;
IF mux_state THEN status_word := status_word + 16; END_IF;
IF bDataReady THEN status_word := status_word + 32; END_IF;

(* 9.2 xMeasure Array - RAW-WERTE [1-9] *)
xMeasure[1] := WORD_TO_INT(S_Vorlauf);
xMeasure[2] := WORD_TO_INT(S_Aussen);
xMeasure[3] := WORD_TO_INT(S_Innen);
xMeasure[4] := WORD_TO_INT(S_Kessel);
xMeasure[5] := WORD_TO_INT(S_Warmw);
xMeasure[6] := WORD_TO_INT(S_Oeltank);        (* ← FIXED: War vorher nicht zugewiesen *)
xMeasure[7] := WORD_TO_INT(S_Ruecklauf);
xMeasure[8] := WORD_TO_INT(S_Solar);          (* ← FIXED: War vorher nicht zugewiesen *)
xMeasure[9] := 0;  (* Reserved *)

(* 9.3 xMeasure Array - STATUS & KONTROLLE [10-18] *)
xMeasure[10] := hour_of_day;
xMeasure[11] := status_word;
xMeasure[12] := REAL_TO_INT(temp_diff_ww * 100.0);
xMeasure[13] := REAL_TO_INT(temp_kessel * 100.0);
xMeasure[14] := REAL_TO_INT(temp_warmw * 100.0);
xMeasure[15] := REAL_TO_INT(temp_vorlauf * 100.0);
xMeasure[16] := BYTE_TO_INT(VERSION_MAJOR) * 256 + BYTE_TO_INT(VERSION_MINOR);
xMeasure[17] := BYTE_TO_INT(VERSION_PATCH);
xMeasure[18] := 0;  (* Serial/Reserved *)

(* 9.4 xMeasure Array - RUNTIME & CYCLES [19-24] *)
xMeasure[19] := UDINT_TO_INT(LIMIT(0, WW_Runtime, 32767));
xMeasure[20] := UDINT_TO_INT(LIMIT(0, HK_Runtime, 32767));
xMeasure[21] := UDINT_TO_INT(LIMIT(0, BR_Runtime, 32767));
xMeasure[22] := UDINT_TO_INT(LIMIT(0, WW_Cycles, 32767));
xMeasure[23] := UDINT_TO_INT(LIMIT(0, HK_Cycles, 32767));
xMeasure[24] := UDINT_TO_INT(LIMIT(0, BR_Cycles, 32767));

(* 9.5 xMeasure Array - REASON BYTES [25-27] *)
xMeasure[25] := BYTE_TO_INT(bWW_Reason);
xMeasure[26] := BYTE_TO_INT(bHK_Reason);
xMeasure[27] := BYTE_TO_INT(bBR_Reason);      (* ← FIXED: War vorher nicht zugewiesen *)

(* 9.6 xMeasure Array - BERECHNETE TEMPERATUREN × 100 [28-31] *)
xMeasure[28] := REAL_TO_INT(temp_aussen * 100.0);
xMeasure[29] := REAL_TO_INT(temp_innen * 100.0);   (* ← FIXED: War vorher nicht zugewiesen *)
xMeasure[30] := REAL_TO_INT(temp_ruecklauf * 100.0);
xMeasure[31] := REAL_TO_INT(temp_solar * 100.0);   (* ← FIXED: War vorher nicht zugewiesen *)

(* 9.7 xMeasure Array - PHYSICAL OUTPUTS [32] *)
xMeasure[32] := BYTE_TO_INT(%QB0);

(* 9.8 xSystem Array - DIAGNOSE *)
xSystem[1] := UDINT_TO_INT(uptime_seconds MOD 65536);
xSystem[2] := UDINT_TO_INT(uptime_seconds / 65536);
xSystem[3] := 0;  (* Error counter *)
xSystem[4] := 0;  (* CPU load *)
xSystem[5] := 0;  (* Cycle min *)
xSystem[6] := 0;  (* Cycle max *)
xSystem[7] := 0;  (* Cycle avg *)
xSystem[8] := 0;  (* Reserved *)

(* 9.9 xStats Array - BETRIEBSSTUNDEN *)
xStats[1] := UDINT_TO_INT(HK_Runtime / 3600);
xStats[2] := UDINT_TO_INT(WW_Runtime / 3600);
xStats[3] := UDINT_TO_INT(BR_Runtime / 3600);
xStats[4] := UDINT_TO_INT(HK_Cycles);
xStats[5] := UDINT_TO_INT(WW_Cycles);
xStats[6] := UDINT_TO_INT(BR_Cycles);
xStats[7] := 0;  (* Reserved *)
xStats[8] := 0;  (* Reserved *)

(* ========================================================================== *)
(* ENDE PLC_PRG v6.5.4                                                        *)
(* ========================================================================== *)
