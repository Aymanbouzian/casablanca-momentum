# Démarrage simple

Le projet analyse les actions de la Bourse de Casablanca pour un portefeuille virtuel d'un million de dirhams. Il ne passe aucun ordre.

## Sur Windows

1. Décompresse entièrement le ZIP et ouvre le dossier `casablanca_momentum`.
2. Lis `outputs/report.html` et `outputs/ONE_PAGE_SUMMARY.md` : les résultats sont déjà calculés.
3. Lance `SETUP_WINDOWS.cmd` une fois, puis `RUN_WINDOWS.cmd` pour recalculer.
4. Pour d'autres historiques en ZIP, lance `python scripts/import_exchange_zip.py chemin\vers\archive.zip` dans ce dossier. Consulte ensuite `outputs/import_audit.csv` et relance le programme.

Dans PyCharm, choisis `.venv\Scripts\python.exe` et lance `run.py` depuis le dossier du projet.

## Règle de la stratégie

À la fin d'un mois complet, le programme compare chaque action à sa propre valeur douze mois plus tôt. Une hausse supérieure au rendement monétaire de référence donne un signal positif. Il écarte les actions dont les cours ou les échanges ne sont pas assez fiables et retient jusqu'à 40 actions liquides. Il répartit le capital sur les signaux positifs, avec au plus 8 % par action, 30 % par secteur et 95 % au total. Le but de 70 % investi reste impossible s'il n'y a pas assez d'actions positives.

Les petits ajustements sont ignorés pour économiser des frais. Si une position atteint **9,2 % du portefeuille à une clôture**, le programme prévoit une vente vers 8 % pour la séance suivante. Une forte hausse en une séance ou une vente non exécutée peut encore dépasser la limite de 10 %. Le simulateur conserve alors cette journée de dépassement dans son journal.

## Les nouveaux fichiers

L'archive reçue contenait 76 CSV de cours. Les deux originaux ont été conservés, 58 historiques nouveaux ont été importés et 16 fichiers ont été écartés : 13 portaient dans le contenu le ticker d'une autre action, et trois n'avaient qu'un seul cours observé. **Il y a maintenant 60 historiques distincts**, dont 32 passent les filtres à la dernière fin de mois, le 30 octobre 2026.

Pour cette date, une seule action admissible a un signal positif : **SMI**, avec une cible indicative de 8 %. AKDITAL a un signal négatif. Ce constat utilise des cours sans historique complet de dividendes ni taux monétaire MAD fourni. Il ne faut pas le transformer directement en ordre : il faut vérifier le cours actuel, les secteurs, la QMQ et le compte réel.

`model_comparison_training.csv` couvre la période antérieure au 1er mai 2026 ; `model_comparison_same_period.csv` décrit les séances suivantes. Nous avons déjà consulté ces données : cette séparation chronologique n'est donc **pas** un test indépendant. Le signal de douze mois reste la règle par défaut.

Pour comparer au portefeuille réellement détenu, complète un fichier de compte à partir de `data/templates/account.example.json` avec les liquidités, les titres réglés et les ordres en attente. Le tableau `target_holdings_REVIEW_ONLY.csv` reste indicatif. Le modèle d'exécution ne reproduit pas exactement le fixing ni la régularisation automatique de la plateforme.
