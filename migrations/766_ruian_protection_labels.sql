-- The register's protection code list, so a building's protection can be served by name (#2605, spec #2590).
--
-- ruian_building_objects.protection_codes holds the Kod of each ZpusobOchrany of a building: a value of
-- the "způsob ochrany nemovitosti" list, which RÚIAN takes over unchanged from the cadastre (VFR documentation
-- §3.3.19: "Způsob ochrany stavebního objektu je přebírán ze způsobu ochrany budovy, který je spravován v
-- agendovém systému ISKN"). The list is not only heritage: it holds nature, spa, mineral, water-source and
-- geodetic-mark protection too. Labels are ČÚZK's own wording, abbreviations included, transcribed by script
-- from SC_ZP_OCHRANY_NEM.csv (https://services.cuzk.cz/sestavy/cis/SC_ZP_OCHRANY_NEM.zip, read 2026-11-23,
-- 57 rows; RÚIAN's own copy in the ST_UCIS file of 2026-11-03 names all 57 identically). Codes ČÚZK has since
-- closed are seeded too: a building may still carry one.
--
-- The file's TypOchranyKod (památková ochrana, ochrana přírody a krajiny, …) is not stored: the list gives
-- every code exactly one type, so it adds nothing the code does not say (checked on one obec file: all 14
-- code and type pairs seen agree with the list).
--
-- Additive and re-runnable: a row already there takes ČÚZK's label.

insert into public.ruian_code_labels (code_list, code, label, unknown) values
  ('protection', 1, 'menší chráněné území', false),
  ('protection', 2, 'rozsáhlé chráněné území', false),
  ('protection', 3, 'vnitř.lázeň.území, ložis.slatin a rašeliny, ochr.pásmo 1.st.', false),
  ('protection', 4, 'památkově chráněné území', false),
  ('protection', 5, 'značka geodetického bodu a její chráněné území', false),
  ('protection', 6, 'národní park - I.zóna', false),
  ('protection', 7, 'národní park - II.zóna', false),
  ('protection', 8, 'národní park - III.zóna', false),
  ('protection', 9, 'ochranné pásmo národního parku', false),
  ('protection', 10, 'chráněná krajinná oblast - I.zóna', false),
  ('protection', 11, 'chráněná krajinná oblast - II.-IV.zóna', false),
  ('protection', 12, 'národní přírodní rezervace nebo národní přírodní památka', false),
  ('protection', 13, 'přírodní rezervace nebo přírodní památka', false),
  ('protection', 14, 'ochr. pásmo jiného zvlášť chrán. území nebo pam.stromu', false),
  ('protection', 15, 'nemovitá národní kulturní památka', false),
  ('protection', 16, 'pam. rezervace - budova, pozemek v památkové rezervaci', false),
  ('protection', 17, 'pam. zóna - budova, pozemek v památkové zóně', false),
  ('protection', 18, 'nemovitá kulturní památka', false),
  ('protection', 19, 'ochr.pásmo nem.kult.pam.,pam.zóny,rezervace,nem.nár.kult.pam', false),
  ('protection', 20, 'vnitřní území lázeňského místa', false),
  ('protection', 21, 'přír. léč. zdroj nebo zdroj přír. miner. vody', false),
  ('protection', 22, 'ochr. pásmo přír. léčiv. zdroje nebo zdroje přír. miner.vody', false),
  ('protection', 23, 'chr.lož.území, chr.území pro zvl.zásahy do z.kůry', false),
  ('protection', 24, 'chráněná značka geodetického bodu', false),
  ('protection', 25, 'chráněné území značky geodetického bodu', false),
  ('protection', 26, 'pozemek určený k plnění funkcí lesa', false),
  ('protection', 27, 'zemědělský půdní fond', false),
  ('protection', 28, 'ochranné pásmo vodního díla', false),
  ('protection', 29, 'ochranné pásmo vodního zdroje', false),
  ('protection', 30, 'ochranné pásmo vodního díla 1.stupně', false),
  ('protection', 31, 'ochranné pásmo vodního díla 2.stupně', false),
  ('protection', 32, 'ochranné pásmo vodního zdroje 1.stupně', false),
  ('protection', 33, 'ochranné pásmo vodního zdroje 2.stupně', false),
  ('protection', 34, 'evropsky významná lokalita', false),
  ('protection', 35, 'ptačí oblast', false),
  ('protection', 36, 'pozemek určený k plnění funkcí lesa - dočasně odňato', false),
  ('protection', 37, 'zemědělský půdní fond - dočasně odňato', false),
  ('protection', 38, 'národní park', false),
  ('protection', 39, 'chráněná krajinná oblast', false),
  ('protection', 40, 'národní park - zóna přírodní', false),
  ('protection', 41, 'národní park - zóna přírodě blízká', false),
  ('protection', 42, 'národní park - zóna soustředěné péče', false),
  ('protection', 43, 'národní park - zóna kulturní krajina', false),
  ('protection', 44, 'chráněná krajinná oblast - II. zóna', false),
  ('protection', 45, 'chráněná krajinná oblast - III. zóna', false),
  ('protection', 46, 'chráněná krajinná oblast - IV. zóna', false),
  ('protection', 47, 'národní přírodní rezervace', false),
  ('protection', 48, 'národní přírodní památka', false),
  ('protection', 49, 'přírodní rezervace', false),
  ('protection', 50, 'přírodní památka', false),
  ('protection', 51, 'ochranné pásmo národní přírodní rezervace', false),
  ('protection', 52, 'ochranné pásmo národní přírodní památky', false),
  ('protection', 53, 'ochranné pásmo přírodní rezervace', false),
  ('protection', 54, 'ochranné pásmo přírodní památky', false),
  ('protection', 55, 'ochranné pásmo památného stromu', false),
  ('protection', 56, 'chráněná ložisková území', false),
  ('protection', 57, 'chráněná území pro zvl. zásahy do zemské kůry', false)
on conflict (code_list, code) do update set label = excluded.label, unknown = excluded.unknown;
