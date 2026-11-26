<div align="center">

# stonks-bot

**10 000 € deviennent 661 655 € après impôt en 11,9 ans.**
<br>
<sub>Livre US mensuel · 2,5x · Interactive Brokers · PFU 31,4 % · grand livre EUR</sub>

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Statut](https://img.shields.io/badge/statut-paper%20only-8957e5)](results/paper.md)
[![France](https://img.shields.io/badge/France-2026-1f6feb)](results/france.md)
[![Après impôt](https://img.shields.io/badge/apr%C3%A8s%20imp%C3%B4t-66%2C2x-3fb950)](results/eur_ledger.md)
[![English](https://img.shields.io/badge/English-README-8b949e)](README.en.md)

</div>

> [!WARNING]
> C'est un backtest, pas une promesse de rendement. Le livre US a perdu 56,4 % au pire creux (grand livre EUR) et 32,8 % en 2022. À 2,5x, la baisse peut aller plus vite que ta capacité à couper. N'investis pas de l'argent dont tu as besoin.

> [!NOTE]
> Le grand livre EUR compte l'impôt français sur les gains en EUR chaque année, avec une base de coût en EUR au taux de change du jour. Il donne 66,17x pour le livre US et 53,0x pour le livre TECH, contre 70,9x/57,0x pour l'ancienne méthode qui ne convertit qu'à la fin. Voir `docs/accounting.md`. Le livre TECH reste le suivi forward ; le livre US devient le défaut pour un nouvel investisseur.

## Combien ça rapporte

10 000 € au départ, compte-titres ordinaire chez Interactive Brokers, livre US mensuel. Du 2 janvier 2015 au 25 novembre 2026, le capital finit à 66,17x après impôt dans le grand livre EUR, soit 42,3 % par an. Les montants ci-dessous prolongent ce rythme.

| Horizon | Capital final | Gain | QQQ après impôt |
|---|---:|---:|---:|
| 1 mois | 10 299 € | +299 € | 10 119 € |
| 1 an | 14 223 € | +4 223 € | 11 519 € |
| 15 ans | ≈ 1,99 M€ | ≈ +1,98 M€ | ≈ 83 400 € |

<sub>1 mois et 1 an : rythme géométrique moyen sur 11,9 ans, pas une promesse pour chaque période. 15 ans : le même rythme prolongé, aucun backtest ne couvre 15 ans. QQQ paie le même PFU de 31,4 %. Pour un autre capital, les frais fixes changent le résultat.</sub>

![Rendements annuels avant impôt](docs/readme_fr.svg)

<sub>Le graphique montre les années avant PFU du livre TECH historique. Le multiple de 66,17x et le rendement annualisé de 42,3 % sont le livre US, après impôt dans le grand livre EUR.</sub>

## Le principe

1. Chaque fin de mois, le bot compare le rendement 6 mois de SMH à celui de SPY.
2. Si SMH gagne, il garde SMH à 2,5x brut, soit 2,50 € d'exposition pour 1 € de capital, financé à 5 % par an. Sinon, il passe sur un panier de 6 actions momentum de son univers : S&P 500 point-in-time pour le livre US (défaut), 49 valeurs NASDAQ pour TECH (suivi forward). Les poids sont inversement proportionnels à la volatilité.
3. Sous la moyenne mobile 200 jours, il réduit l'exposition de moitié jusqu'au retour au-dessus. Pas de stop suiveur.

Les ordres partent à l'ouverture suivante. La stratégie française paie 5 bps (points de base) de spread, les frais réels du broker, la conversion EUR/USD et le PFU. Le cœur de la stratégie est un ETF semi-conducteurs, pas une collection de réussites choisies après coup.

## Et le court terme ?

Testé et rejeté. Le rebalancement hebdomadaire/quotidien du même livre, le reversal 1/5 jours, le momentum 20 jours et l'effet overnight close-to-open ne survivent pas aux coûts et au PFU. En walk-forward 2019-2026, le meilleur signal close-to-close fait **2,64x après impôt contre 5,49x pour QQQ** ; à coûts doublés il tombe à 1,13x, sous BIL (1,20x). L'overnight fait 1,01x en hypothèse optimiste, 0,82x tous frais, 0,60x à coûts doublés. Le comparator always-overnight perd 88 % sur SPY avant impôt avec 10 bps par côté (20 bps aller-retour). Il n'y a pas de satellite court terme à allouer : le livre mensuel reste la seule stratégie de production. Voir `results/alpha_short.md` après merge.

## Le broker

**Verdict : Interactive Brokers, compte-titres ordinaire, livre US mensuel.**

À 10 000 €, le livre US mensuel chez Interactive Brokers finit à 66,17x après impôt dans le grand livre EUR (661 655 €). Le livre TECH, suivi en forward, fait 53,0x (529 755 €). Le classement des brokers dépend surtout du coût de conversion EUR/USD, pas de la commission par ordre.

| Livre | Multiple EUR ledger | Pire creux | 2022 |
|---|---:|---:|---:|
| **US mensuel (défaut)** | **66,17x** | **-56,9 %** | **-33,7 %** |
| TECH mensuel (forward) | 53,0x | -65,6 % | -52,4 % |
| CORE SMH seul | 49,0x | -64,2 % | -54,2 % |

<sub>Grand livre EUR : impôt annuel sur les gains en EUR, base de coût au taux du jour. Les tableaux brokers ci-dessous restent en mesure terminale, pour comparer les frais.</sub>

| Broker | Variante | Multiple net | Frais | Pire creux | Remarque |
|---|---|---:|---:|---:|---|
| Interactive Brokers | TECH mensuelle | **57,0x** | 26 423 € | -67,8 % | 55,6x à 3 bps auto |
| Interactive Brokers | TECH core-only | **50,9x** | 9 952 € | -68 % | 178 trades, plus simple |
| Trade Republic | TECH core-only | **39,5x** | 33 533 € | -69 % | 1 € par ordre, repli à 25 bps |
| DEGIRO | TECH core-only | **46,3x** | 19 282 € | -69 % | 39,2x à la conversion publiée de 25 bps, pas de PEA |
| XTB | PEA 1x, PUST.PA | **5,8x** | 39 € | -31 % | seulement pour un PEA déjà ouvert |

<sub>Frais cumulés sur 11,9 ans pour 10 000 € de départ. Les multiples sont après PFU, frais et taxe de sortie. Le pire creux est mesuré avant impôt.</sub>

`core-only` veut dire SMH seul, sans panier momentum. Cette variante effectue environ 178 transactions en 11,9 ans, contre 1 836 pour la version mensuelle.

Interactive Brokers gagne parce que la stratégie convertit souvent USD/EUR. À 2 bps manuels, la version mensuelle est devant. Au-delà de 15 bps de conversion, la commande bascule sur la variante core-only. À 25 bps, IBKR core-only fait 39,5x, à égalité avec Trade Republic.

DEGIRO reste à 46,3x avec l'hypothèse de 10 bps, mais tombe à 39,2x avec son AutoFX publié de 25 bps. Saxo (36,4x), Bourse Direct (34,7x), Fortuneo (24,9x) et BoursoBank (22,7x) restent derrière sur la variante core-only. À 10 000 €, les commissions fixes et la conversion pèsent plus que le taux de courtage affiché.

La stratégie US mensuelle chez IBKR est le meilleur backtest et le défaut pour un nouvel investisseur : **66,17x après impôt dans le grand livre EUR** (70,9x en mesure terminale), maxDD -56,9 %, 7 fenêtres de 5 ans sur 7 battent SPY. Le livre TECH reste le suivi forward (1/5 dates validées) ; `--market TECH` le restaure.

## Les impôts

**Verdict : compte-titres ordinaire. Le PEA ne peut pas porter le cœur SMH, encore moins à 2,5x.**

| Enveloppe | Taux 2026 | À retenir |
|---|---|---|
| CTO | **31,4 %** | PFU (prélèvement forfaitaire unique) : 12,8 % d'impôt sur le revenu et 18,6 % de prélèvements sociaux sur le gain net annuel. Les moins-values se reportent 10 ans. |
| PEA | **18,6 %** | Plan d'épargne en actions. Prélèvements sociaux seulement après 5 ans. Pas d'actions US en direct, pas de SMH, pas de levier au niveau du compte. Plafond de 150 000 €. |

Dans ce modèle, le CTO est la seule enveloppe qui peut porter SMH à 2,5x. Le PEA sauve 12,8 points de PFU, mais il ne peut pas acheter le cœur de la stratégie. Le meilleur PEA tech 1x, PUST.PA, fait 5,8x après social, contre 66,17x pour le CTO (livre US, grand livre EUR).

La FTT (taxe sur les transactions financières) de 0,4 % s'applique aux achats d'actions françaises depuis avril 2025. Les stratégies US et TECH n'en paient pas. La stratégie française paie 5 757 € de FTT pour 10 000 € de départ chez IBKR. Le PFU 2026 est modélisé à 31,4 %, pas 30 %.

### Le risque fiscal américain et le PEA

Un investisseur français non-résident américain qui détient SMH/SPY/QQQ directement a des actifs **US-situs**. Sans traité, l'abattement successoral américain n'est que de 60 000 $ et le taux monte à 40 %. Le traité France-US de 2004 accorde un crédit prorata qui annule l'impôt américain dans nos exemples, mais il faut le documenter (Form 706-NA/8833) et il ne supprime pas les droits de succession français. Un cœur UCITS irlandais supprime l'exposition US-situs, mais SMH.L ne retient que 64 % du rendement de SMH sur la fenêtre commune (3,61x vs 5,63x) : le coût en rendement est plus grand que l'économie d'impôt. Le PEA ne peut pas porter un ETF semi-conducteurs pur : PUST.PA (Nasdaq-100) fait 5,77x après social contre 66,17x pour le CTO. Détails : `results/estate_tax.md`, `results/ucits_core.md`, `results/pea_semis.md`. Ce n'est pas un conseil fiscal ou juridique.

## Les indices et ETF

| Ticker | Rôle | Ce que c'est |
|---|---|---|
| SMH | cœur | VanEck Semiconductor ETF. Le moteur de la stratégie, plus concentré que SOXX, son concurrent. |
| SPY | signal | S&P 500. Le bot compare SMH à SPY sur 6 mois. Le panier momentum de la stratégie US sort de cet indice. |
| QQQ | benchmark TECH | Nasdaq-100. 5,38x après impôt sur la période. |
| CAC 40 | benchmark France | 2,13x après impôt. La stratégie française fait 6,4x à 16,2x selon le broker. |
| PUST.PA | proxy PEA | Amundi PEA Nasdaq-100. 5,8x après social, sans semi-conducteurs purs ni levier. |

La stratégie française combine le CAC 40 et le cœur SMH. Elle finit à 6,4x chez Bourse Direct, 12,5x chez Trade Republic et 16,2x chez IBKR, après FTT et PFU. Elle perd contre la stratégie TECH, mais elle reste devant le CAC seul.

## Quel profil ?

| Profil | Allocation | EUR ledger | Pire creux | 2022 |
|---|---|---:|---:|---:|
| **max_growth** (défaut) | US mensuel 2,5x | **66,17x** | -56,9 % | -33,7 % |
| balanced (opt-in papier) | 70 % livre US + 30 % SPY | 36,6x | -46,9 % | -27,4 % |
| conservative (opt-in papier) | US mensuel 1,5x | 19,3x | -40,4 % | -20,3 % |

`max_growth` maximise le capital mais perd plus de la moitié au pire creux. `balanced` abandonne environ la moitié du capital final pour 10 points de baisse en moins ; c'est le profil d'un humain qui doit rester investi. Les deux opt-in sont post-hoc et étiquetés `paper`, pas validés forward. `uv run python -m bot.cli backtest --country fr --profile balanced` reproduit le profil balanced ; `--profile monthly --leverage 1.5` reproduit le conservateur. Détails : `docs/profiles.md`.

## Le risque, sans filtre

- Pire creux : **-56,9 %** sur le livre US (grand livre EUR). En 2022 : -33,7 %. En 2018 : -31,1 %. Le livre TECH fait -65,6 % au pire, -52,4 % en 2022 et -10,5 % en 2018.
- Queue de risque : dans le bootstrap US (blocs de 10 jours), 5 % des trajectoires finissent sous **2,58x** après impôt proportionnel, médiane 64,4x. Le livre TECH reste plus fragile : p5 1,93x, creux p5 -90,9 %.
- Régularité : le livre US bat SPY après impôt sur **7 fenêtres de 5 ans sur 7**, médiane 6,83x contre 1,74x. Le livre TECH bat QQQ sur 84 fenêtres sur 84, médiane 5,67x contre 1,97x.
- Biais de survie : TECH utilise une liste statique de 49 valeurs NASDAQ choisies aujourd'hui. Le contrôle PIT Nasdaq-100 retient **95 %** du grand livre EUR statique (50,3x vs 53,0x) ; le sleeve n'est donc pas principalement un artefact de survie, mais il reste derrière le livre US all-PIT. US utilise la composition historique du S&P, mais environ 18 % des membres 2015-2026 n'ont pas d'historique Yahoo et sont exclus. Le backtest est un contrôle de cohérence, pas une preuve.
- Hedge EUR/USD : **rejeté**. À 100 %, le livre US tombe à 45,4x et le pire creux se creuse à -67 % ; le dollar a servi d'amortisseur en 2022. Rester non couvert. `results/fx_hedge.md`.
- Tax-loss harvesting : **rejeté** après frais. C'est surtout du report d'impôt ; le basis plus bas revient en taxe de sortie. `results/tax_harvest.md`.
- Validation : le suivi forward papier TECH est à 1 date valide sur les 5 demandées. Le suivi forward US vient de démarrer ; il n'a pas encore de date clôturée.

Ce n'est pas un conseil en investissement. C'est un backtest reproductible, avec ses hypothèses et ses erreurs possibles.

## Lancer

```bash
uv sync
uv run python -m bot.cli ingest
uv run python -m bot.cli backtest --country fr --capital 10000 --random-seeds 0
uv run python -m bot.cli pnl --country fr --capital 10000
```

`--country fr` utilise le livre US mensuel, Interactive Brokers et un CTO par défaut. La commande affiche le multiple terminal et le **grand livre EUR** (66,17x pour US). `--market TECH` restaure le livre TECH du suivi forward, `--profile tax` la variante core-only, `--fx-bps` ton vrai coût de conversion. Aucun ordre n'est envoyé, aucune clé n'est nécessaire.

## Le pilote papier

```bash
uv run python scripts/live.py plan --book monthly --market US
uv run python scripts/live.py run  --book monthly --market US --paper
uv run python scripts/live.py status  --book monthly --market US
uv run python scripts/live.py reconcile --book monthly --market US
uv run python scripts/forward_daily.py --paper --tax
```

Le dry-run est le défaut ; `--paper` est le seul mode qui écrit. `forward_daily.py` enchaîne US + TECH (+ tax) après la clôture, rafraîchit seulement les barres périmées, réconcilie et met à jour le gate ; une automation weekday tourne à 21:45 UTC. L'état est atomique sous `state/live/`, les ordres sont idempotents, les rails bloquent les données périmées, les jours fériés, le gross/turnover excessif et les doublons. Le ledger papier est **pré-impôt et au coût moyen** : il ne remplace pas le grand livre EUR après impôt. Aucun broker réel, aucune clé.

## Le conseiller et l'exploitation

```bash
uv run python scripts/advise.py --profile max_growth --horizon 12 \
  --drawdown-tolerance 0.60 --capital 10000 --wrapper cto --as-of 2026-11-25
uv run python scripts/ops.py status
uv run python scripts/ops.py doctor
uv run python scripts/ops.py backup --out /var/backups/stonks-bot
```

`advise.py` transforme le profil, l'horizon et la tolérance au risque en allocation et liste d'ordres papier, avec un simulateur de retrait sur 15 ans. `ops.py` donne le status, le diagnostic, les sauvegardes vérifiées et le runbook. Aucun ordre réel.

Les détails techniques sont dans le [README anglais](README.en.md), les [résultats brokers](results/brokers.md), les [résultats France](results/france.md), le [test de robustesse](results/robustness_best.md) et le [résumé v2](results/v2_summary.md), le [résumé v3](results/v3_summary.md), les [résumés v4](results/v4_summary.md), [v5](results/v5_summary.md), [v6](results/v6_summary.md), la [matrice des commandes](docs/feature_matrix.md) et la [fiche de release v7](RELEASE.md).

## Release v7

La fiche [RELEASE.md](RELEASE.md) rassemble les chiffres, les verdicts et la checklist de release. Pour vérifier une copie locale avant tag :

```bash
uv run python scripts/release_check.py
```

Le contrôle lance tous les tests, l'acceptance, le baseline EUR, les plans papier US/TECH, le gate forward et le dashboard, puis écrit `results/release_check.md`. Le gate reste **WAIT US 0/5, TECH 1/5** : c'est une information de suivi, pas une preuve forward. Aucun tag ni release GitHub n'est créé par cette branche.

<sub>Les performances passées ne préjugent pas des performances futures. Dernière mise à jour : 26 novembre 2026, données au 25 novembre 2026. Résumé v2 et tests : `results/v2_summary.md`.</sub>
