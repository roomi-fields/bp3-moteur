#!/usr/bin/env python3
"""Regrave les références que le moteur PRODUIT, avec le binaire courant, sur une COPIE.

Deux jeux vivent dans `test-data/` :

- les SYNTHÈSES `<nom>.json`, que le moteur écrit lui-même à côté de `-so.<nom>` — et il ne
  l'écrit que si la liste d'événements est demandée (`SaveLoads1.c:801`, `EventListOn`, donc
  `--eventlistout`). Leur contenu ne dépend pas des réglages passés : mesuré sur deux jeux
  distincts, même empreinte ;
- les ORACLES DE DÉRIVATION `oracles-derivation/<grammaire>.<mode>.json`, que ce script compose
  depuis la production texte et les jetons minutés. `single` n'est pas une action du binaire :
  c'est `produce` avec `MaxItemsProduce` forcé à 1.

La campagne tourne dans une COPIE du dossier, hors du dépôt : une exécution du moteur réécrit
les synthèses là où elle trouve les auxiliaires, et le dossier de référence ne doit jamais être
ce lieu-là. Le résultat n'y est reporté qu'avec `poser`, après comparaison.

    python3 scripts/regraver-les-references.py          # mesure seule, rien n'est écrit
    python3 scripts/regraver-les-references.py poser    # reporte dans test-data/
"""
import hashlib, json, os, pathlib, re, shutil, subprocess, sys, tempfile

MOTEUR = pathlib.Path(__file__).resolve().parent.parent
BP3 = MOTEUR / "builds/v3.5.4-build.1/bp3"
TD = MOTEUR / "test-data"
ORACLES = TD / "oracles-derivation"
RUN = MOTEUR / "capture-run"

SYNTHESES = ["dhati", "tryCsoundObjects", "tryKeyMap"]


def version_du_binaire() -> str:
    r = subprocess.run([str(BP3), "--short-version"], capture_output=True, text=True)
    # Le binaire ajoute sa ligne de sortie de commande ; la version est le premier N.N.N rencontre.
    m = re.search(r"\d+\.\d+\.\d+", r.stdout + r.stderr)
    if m is None:
        raise SystemExit("le binaire ne dit pas sa version : " + (r.stdout + r.stderr)[:200])
    return "bp3 natif v" + m.group(0)


def empreinte(p) -> str:
    p = pathlib.Path(p)
    return hashlib.md5(p.read_bytes()).hexdigest()[:12] if p.exists() else "ABSENT"


def gravee(chemin_relatif: str) -> str:
    r = subprocess.run(["git", "show", f"HEAD:{chemin_relatif}"], cwd=MOTEUR, capture_output=True)
    return hashlib.md5(r.stdout).hexdigest()[:12] if r.returncode == 0 else "ABSENT"


def synthese(copie, travail, nom, reglages):
    """Une exécution ; rend le contenu du `<nom>.json` que le moteur a écrit dans la copie."""
    cible = copie / f"{nom}.json"
    if cible.exists():
        os.remove(cible)
    subprocess.run(
        [str(BP3), "produce", "-e", "-gr", str(copie / "-gr.trySrand"), "--seed", "1",
         "-se", str(copie / f"-se.{reglages}"),
         "-al", str(copie / f"-al.{nom}"), "-so", str(copie / f"-so.{nom}"),
         "-o", str(travail / "items.txt"), "--eventlistout", str(travail / "evenements.json")],
        capture_output=True, timeout=120, cwd=RUN)
    return cible.read_bytes() if cible.exists() else None


def derivation(copie, travail, fiche):
    """Rejoue un oracle de dérivation ; rend (items, jetons)."""
    cfg = fiche["config"]
    reglages = copie / cfg["settings"]
    if fiche["mode"] == "single":
        d = json.load(open(reglages, encoding="utf-8"))
        d.setdefault("MaxItemsProduce", {})["value"] = "1"
        reglages = travail / (reglages.name + ".1item")
        json.dump(d, open(reglages, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    texte, jetons = travail / "items.txt", travail / "jetons.json"
    for p in (texte, jetons):
        if p.exists():
            os.remove(p)
    args = [str(BP3), "produce" if fiche["mode"] == "single" else "produce-all", "-e",
            "-gr", str(copie / fiche["gr_file"]), "--seed", str(cfg["seed"]),
            "-se", str(reglages), "-al", str(copie / cfg["alphabet"])]
    if cfg.get("sound_objects"):
        args += ["-so", str(copie / cfg["sound_objects"])]
    args += ["-o", str(texte)]
    if fiche["mode"] == "single":
        args += ["--tokensout", str(jetons)]
    subprocess.run(args, capture_output=True, timeout=180, cwd=RUN)
    items = [l for l in texte.read_text(encoding="utf-8", errors="replace").splitlines()
             if l.strip()] if texte.exists() else []
    toks = []
    if jetons.exists():
        d = json.load(open(jetons))
        ev = d["events"] if isinstance(d, dict) and "events" in d else d
        toks = [[e.get("token"), e.get("start"), e.get("end")] for e in ev]
    return items, toks


def main():
    poser = sys.argv[1:] == ["poser"]
    version = version_du_binaire()
    ecarts = 0

    with tempfile.TemporaryDirectory(prefix="regravure-") as t:
        travail = pathlib.Path(t)
        copie = travail / "test-data"
        shutil.copytree(TD, copie)

        print(f"— SYNTHÈSES, écrites par le moteur ({version})")
        contenus = {}
        for nom in SYNTHESES:
            a = synthese(copie, travail, nom, "trySrand")
            autre = nom if (copie / f"-se.{nom}").exists() else "trySrand"
            b = synthese(copie, travail, nom, autre)
            if a is None:
                print(f"  {nom:<20} ⛔ le moteur n'a rien écrit")
                ecarts += 1
                continue
            stable = "stable" if a == b else "⛔ DÉPEND DES RÉGLAGES"
            ecrit = hashlib.md5(a).hexdigest()[:12]
            grave = gravee(f"test-data/{nom}.json")
            print(f"  {nom:<20} gravé {grave} → {ecrit}  {stable}"
                  f"{'  (inchangé)' if grave == ecrit else ''}")
            contenus[nom] = a

        print(f"— ORACLES DE DÉRIVATION")
        neufs = {}
        for chemin in sorted(ORACLES.glob("*.json")):
            ancien = json.load(open(chemin, encoding="utf-8"))
            items, toks = derivation(copie, travail, ancien)
            memes = items == ancien["items"] and toks == (ancien.get("tokens") or [])
            print(f"  {chemin.name:<30} items {len(ancien['items'])}→{len(items)} · "
                  f"jetons {len(ancien.get('tokens') or [])}→{len(toks)} · "
                  f"{'contenu identique' if memes else '⛔ CONTENU DIFFÉRENT'}")
            if not memes:
                ecarts += 1
                print(f"     gravé : {ancien['items']} / {len(ancien.get('tokens') or [])} jetons")
                print(f"     neuf  : {items} / {len(toks)} jetons")
            neuf = dict(ancien)
            neuf["source"] = version
            neuf["items"], neuf["n_items"] = items, len(items)
            neuf["tokens"], neuf["n_tokens"] = toks, len(toks)
            neufs[chemin] = neuf

    if not poser:
        print("\nMesure seule — rien n'a été écrit. Relancer avec `poser` pour reporter.")
        return 1 if ecarts else 0

    for nom, contenu in contenus.items():
        (TD / f"{nom}.json").write_bytes(contenu)
    for chemin, neuf in neufs.items():
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump(neuf, f, indent=1, ensure_ascii=False)
            f.write("\n")
    print(f"\n→ {len(contenus)} synthèse(s) et {len(neufs)} oracle(s) portent la sortie de {version}.")
    return 1 if ecarts else 0


if __name__ == "__main__":
    sys.exit(main())
