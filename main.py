import os
import discord
from discord import app_commands
from discord.ext import commands
import random
import asyncio
from keep_alive import keep_alive
import sqlite3
from datetime import datetime

token = os.environ['TOKEN_BOT_DISCORD']

# Dictionnaire pour stocker les duels en cours
duels = {}

# Emojis pour la grille de morpion
EMOJIS_MORPION = {
    "X": "❌",
    "O": "⭕",
    " ": "◻️"
}

# Commission du croupier
COMMISSION = 0.05

# Connexion à la base de données (pour garder des stats)
conn = sqlite3.connect("tictactoe_stats.db")
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS parties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    joueur1_id INTEGER NOT NULL,
    joueur2_id INTEGER NOT NULL,
    montant INTEGER NOT NULL,
    gagnant_id INTEGER,
    est_nul BOOLEAN NOT NULL,
    date TIMESTAMP NOT NULL
)
""")
conn.commit()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="/", intents=intents)

# --- Logique du jeu de morpion ---
def check_win(board, player):
    win_conditions = [
        (0, 1, 2), (3, 4, 5), (6, 7, 8),
        (0, 3, 6), (1, 4, 7), (2, 5, 8),
        (0, 4, 8), (2, 4, 6),
    ]
    for condition in win_conditions:
        if board[condition[0]] == board[condition[1]] == board[condition[2]] == player:
            return True
    return False

def check_draw(board):
    return " " not in board

def create_board_display(board):
    board_display = ""
    for i in range(9):
        board_display += EMOJIS_MORPION[board[i]]
        if (i + 1) % 3 == 0:
            board_display += "\n"
    return board_display

def create_board_embed(board, title, description, color, turn=None):
    embed = discord.Embed(
        title=title,
        description=description,
        color=color
    )
    embed.add_field(name="Grille de jeu", value=create_board_display(board), inline=False)
    if turn:
        embed.add_field(name="Tour de", value=f"{turn.mention}", inline=False)
    return embed

# --- Vues Discord ---
class TicTacToeView(discord.ui.View):
    def __init__(self, duel_data):
        super().__init__(timeout=300)
        self.duel_data = duel_data
        self.board = [" " for _ in range(9)]
        self.joueur1 = duel_data["joueur1"]
        self.joueur2 = duel_data["joueur2"]
        
        self.joueur_actif = random.choice([self.joueur1, self.joueur2])
        self.symboles = {
            self.joueur1.id: "X",
            self.joueur2.id: "O"
        }
        
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        for i in range(9):
            row = i // 3
            button = discord.ui.Button(
                emoji=EMOJIS_MORPION[self.board[i]],
                style=discord.ButtonStyle.secondary,
                custom_id=f"case_{i}",
                disabled=self.board[i] != " ",
                row=row
            )
            button.callback = self.on_button_click
            self.add_item(button)

    async def on_button_click(self, interaction: discord.Interaction):
        if interaction.user.id != self.joueur_actif.id:
            await interaction.response.send_message("❌ Ce n'est pas ton tour !", ephemeral=True)
            return

        case_index = int(interaction.data["custom_id"].split("_")[1])
        symbole = self.symboles[self.joueur_actif.id]
        self.board[case_index] = symbole

        if check_win(self.board, symbole):
            await self.end_game(interaction, self.joueur_actif, is_draw=False)
            return

        if check_draw(self.board):
            await self.end_game(interaction, None, is_draw=True)
            return

        # Passe le tour au joueur suivant
        self.joueur_actif = self.joueur2 if self.joueur_actif.id == self.joueur1.id else self.joueur1
        self.update_buttons()
        
        embed = create_board_embed(
            self.board,
            f"⚔️ Duel entre {self.joueur1.display_name} (❌) et {self.joueur2.display_name} (⭕)",
            "Le jeu est en cours. Fais ton coup !",
            discord.Color.blue(),
            turn=self.joueur_actif
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def end_game(self, interaction: discord.Interaction, winner, is_draw):
        if is_draw:
            title = "🤝 Match nul !"
            description = f"La partie entre {self.joueur1.mention} et {self.joueur2.mention} se termine par un match nul."
            color = discord.Color.greyple()
            gagnant_id = None
        else:
            montant = self.duel_data["montant"]
            gain_net = int(montant * 2 * (1 - COMMISSION))
            title = f"🎉 Victoire de {winner.display_name} !"
            description = (
                f"{winner.mention} remporte le duel et gagne **{gain_net:,}** kamas (après 5% de commission).\n\n"
                f"Félicitations !"
            ).replace(",", " ")
            color = discord.Color.green()
            gagnant_id = winner.id
        
        embed = create_board_embed(self.board, title, description, color)
        await interaction.response.edit_message(embed=embed, view=None)

        # Enregistrement dans la base de données
        now = datetime.utcnow()
        try:
            c.execute(
                "INSERT INTO parties (joueur1_id, joueur2_id, montant, gagnant_id, est_nul, date) VALUES (?, ?, ?, ?, ?, ?)",
                (self.joueur1.id, self.joueur2.id, self.duel_data["montant"], gagnant_id, is_draw, now)
            )
            conn.commit()
        except Exception as e:
            print("❌ Erreur lors de l'insertion dans la base de données:", e)

        # Suppression du duel de la liste en cours
        duels.pop(interaction.message.id, None)

class RejoindreView(discord.ui.View):
    def __init__(self, message_id, joueur1, montant):
        super().__init__(timeout=300)
        self.message_id_initial = message_id
        self.joueur1 = joueur1
        self.montant = montant
        self.joueur2 = None
        self.croupier = None
        self.duel_data = {
            "joueur1": self.joueur1,
            "montant": self.montant,
            "joueur2": self.joueur2,
            "croupier": self.croupier,
            "message_id_initial": self.message_id_initial
        }

    @discord.ui.button(label="🎯 Rejoindre le duel", style=discord.ButtonStyle.green, custom_id="rejoindre_duel")
    async def rejoindre(self, interaction: discord.Interaction, button: discord.ui.Button):
        joueur2 = interaction.user
        
        if joueur2.id == self.joueur1.id:
            await interaction.response.send_message("❌ Tu ne peux pas rejoindre ton propre duel.", ephemeral=True)
            return
        
        # Vérification si le joueur est déjà dans un duel
        for data in duels.values():
            if data["joueur1"].id == joueur2.id or (data["joueur2"] and data["joueur2"].id == joueur2.id):
                await interaction.response.send_message("❌ Tu participes déjà à un autre duel.", ephemeral=True)
                return

        self.joueur2 = joueur2
        self.duel_data["joueur2"] = joueur2
        
        self.children[0].disabled = True
        
        self.add_item(discord.ui.Button(label="🎲 Rejoindre en tant que Croupier", style=discord.ButtonStyle.secondary, custom_id="rejoindre_croupier"))
        self.children[-1].callback = self.rejoindre_croupier

        embed = interaction.message.embeds[0]
        embed.title = f"⚔️ Duel entre {self.joueur1.display_name} et {self.joueur2.display_name}"
        embed.set_field_at(1, name="👤 Joueur 2", value=f"{self.joueur2.mention}", inline=True)
        embed.set_field_at(2, name="Status", value="🕓 Un croupier est attendu pour lancer le duel.", inline=False)
        embed.set_footer(text="Cliquez sur le bouton pour rejoindre en tant que croupier.")
        
        role_croupier = discord.utils.get(interaction.guild.roles, name="croupier")
        contenu_ping = f"{role_croupier.mention} — Un nouveau duel est prêt ! Un croupier est attendu." if role_croupier else ""
        
        await interaction.response.edit_message(
            content=contenu_ping,
            embed=embed,
            view=self,
            allowed_mentions=discord.AllowedMentions(roles=True)
        )

    async def rejoindre_croupier(self, interaction: discord.Interaction):
        role_croupier = discord.utils.get(interaction.guild.roles, name="croupier")
        if not role_croupier or role_croupier not in interaction.user.roles:
            await interaction.response.send_message("❌ Tu n'as pas le rôle de `croupier` pour rejoindre ce duel.", ephemeral=True)
            return

        if self.croupier:
            await interaction.response.send_message("❌ Un croupier a déjà rejoint le duel.", ephemeral=True)
            return
            
        self.croupier = interaction.user
        self.duel_data["croupier"] = self.croupier
        
        embed = interaction.message.embeds[0]
        embed.set_field_at(2, name="Status", value=f"✅ Prêt à jouer ! Croupier : {self.croupier.mention}", inline=False)
        embed.set_footer(text="Le croupier peut lancer la partie.")
        
        self.children[-1].disabled = True
        lancer_button = discord.ui.Button(label="🎮 Lancer la partie", style=discord.ButtonStyle.success, custom_id="lancer_partie", row=1)
        lancer_button.callback = self.lancer_partie
        self.add_item(lancer_button)
        
        await interaction.response.edit_message(content="", embed=embed, view=self)

    async def lancer_partie(self, interaction: discord.Interaction):
        if interaction.user.id != self.croupier.id:
            await interaction.response.send_message("❌ Seul le croupier peut lancer la partie.", ephemeral=True)
            return

        if not self.joueur2:
            await interaction.response.send_message("❌ Le duel n'est pas prêt. Il faut deux joueurs.", ephemeral=True)
            return

        await interaction.response.defer()

        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(view=self)

        tictactoe_view = TicTacToeView(self.duel_data)
        embed = create_board_embed(
            tictactoe_view.board,
            f"⚔️ Duel entre {self.joueur1.display_name} (❌) et {self.joueur2.display_name} (⭕)",
            f"Le joueur qui commence est {tictactoe_view.joueur_actif.mention}.",
            discord.Color.blue(),
            turn=tictactoe_view.joueur_actif
        )
        await interaction.channel.send(embed=embed, view=tictactoe_view)
        
        try:
            await interaction.message.delete()
        except discord.NotFound:
            pass

class StatsView(discord.ui.View):
    def __init__(self, ctx, entries, page=0):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.entries = entries
        self.page = page
        self.entries_per_page = 10
        self.max_page = (len(entries) - 1) // self.entries_per_page
        self.update_buttons()

    def update_buttons(self):
        self.first_page.disabled = self.page == 0
        self.prev_page.disabled = self.page == 0
        self.next_page.disabled = self.page == self.max_page
        self.last_page.disabled = self.page == self.max_page
        self.stop_button.disabled = False
        
    def get_embed(self):
        embed = discord.Embed(title="📊 Statistiques Morpion", color=discord.Color.gold())
        start = self.page * self.entries_per_page
        end = start + self.entries_per_page
        slice_entries = self.entries[start:end]

        if not slice_entries:
            embed.description = "Aucune donnée à afficher."
            return embed

        description = ""
        for i, (user_id, victoires, nuls, defaites, total_parties) in enumerate(slice_entries):
            rank = self.page * self.entries_per_page + i + 1
            winrate = (victoires / total_parties * 100) if total_parties > 0 else 0.0
            description += (
                f"**#{rank}** <@{user_id}> | "
                f"**Victoires**: `{victoires}` | **Nuls**: `{nuls}` | **Défaites**: `{defaites}`\n"
                f"**Winrate**: `{winrate:.1f}%` (**{victoires}**/**{total_parties}**)\n"
            )
            if i < len(slice_entries) - 1:
                description += "─" * 20 + "\n"

        embed.description = description
        embed.set_footer(text=f"Page {self.page + 1}/{self.max_page + 1}")
        return embed

    @discord.ui.button(label="⏮️", style=discord.ButtonStyle.secondary)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.max_page:
            self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.secondary)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = self.max_page
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, custom_id="stop_stats")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Fermeture des statistiques.", embed=None, view=None)

# --- Commandes du bot ---
@bot.tree.command(name="duel", description="Lancer un duel de morpion avec un montant.")
@app_commands.describe(montant="Montant misé en kamas")
async def duel(interaction: discord.Interaction, montant: int):
    if not isinstance(interaction.channel, discord.TextChannel) or interaction.channel.name != "morpion":
        await interaction.response.send_message("❌ Cette commande ne peut être utilisée que dans le salon #morpion.", ephemeral=True)
        return
    
    if montant <= 0:
        await interaction.response.send_message("❌ Le montant doit être supérieur à 0.", ephemeral=True)
        return

    for duel_data in duels.values():
        if duel_data["joueur1"].id == interaction.user.id or (
            "joueur2" in duel_data and duel_data["joueur2"] and duel_data["joueur2"].id == interaction.user.id
        ):
            await interaction.response.send_message(
                "❌ Tu participes déjà à un autre duel. Termine-le ou utilise `/quit` pour l'annuler.",
                ephemeral=True
            )
            return
            
    embed = discord.Embed(
        title="⚔️ Nouveau Duel Morpion en attente de joueur",
        description=f"{interaction.user.mention} a misé **{montant:,}** kamas pour un duel.",
        color=discord.Color.orange()
    )
    embed.add_field(name="👤 Joueur 1", value=f"{interaction.user.mention}", inline=True)
    embed.add_field(name="👤 Joueur 2", value="🕓 En attente...", inline=True)
    embed.add_field(name="Status", value="🕓 En attente d'un second joueur.", inline=False)
    embed.set_footer(text="Cliquez sur le bouton pour rejoindre le duel.")

    view = RejoindreView(message_id=None, joueur1=interaction.user, montant=montant)
    
    role_membre = discord.utils.get(interaction.guild.roles, name="membre")
    contenu_ping = f"{role_membre.mention} — Un nouveau duel est prêt ! Un joueur est attendu." if role_membre else ""
    
    await interaction.response.send_message(content=contenu_ping, embed=embed, view=view, allowed_mentions=discord.AllowedMentions(roles=True))
    
    message = await interaction.original_response()
    view.message_id_initial = message.id
    duels[message.id] = view.duel_data
    
@bot.tree.command(name="quit", description="Annule le duel en cours que tu as lancé.")
async def quit_duel(interaction: discord.Interaction):
    duel_a_annuler_id = None
    is_joueur2 = False

    for message_id, duel_data in duels.items():
        if duel_data["joueur1"].id == interaction.user.id:
            duel_a_annuler_id = message_id
            break
    
    if not duel_a_annuler_id:
        for message_id, duel_data in duels.items():
            if "joueur2" in duel_data and duel_data["joueur2"] and duel_data["joueur2"].id == interaction.user.id:
                duel_a_annuler_id = message_id
                is_joueur2 = True
                break

    if duel_a_annuler_id is None:
        await interaction.response.send_message("❌ Tu n'as aucun duel en attente à annuler.", ephemeral=True)
        return

    if not is_joueur2:
        duel_data = duels.pop(duel_a_annuler_id)
        try:
            message_initial = await interaction.channel.fetch_message(duel_a_annuler_id)
            embed_initial = message_initial.embeds[0]
            embed_initial.color = discord.Color.red()
            embed_initial.title += " (Annulé)"
            embed_initial.description = "⚠️ Ce duel a été annulé par son créateur."
            await message_initial.edit(embed=embed_initial, view=None)
        except Exception:
            pass
        await interaction.response.send_message("✅ Ton duel a bien été annulé.", ephemeral=True)
    else:
        duel_data = duels.pop(duel_a_annuler_id)
        try:
            message_initial = await interaction.channel.fetch_message(duel_a_annuler_id)
            joueur1 = duel_data["joueur1"]
            montant = duel_data["montant"]
            
            new_embed = discord.Embed(
                title=f"⚔️ Nouveau Duel Morpion en attente de joueur",
                description=f"{joueur1.mention} a misé **{montant:,}** kamas pour un duel.",
                color=discord.Color.orange()
            )
            new_embed.add_field(name="👤 Joueur 1", value=f"{joueur1.mention}", inline=True)
            new_embed.add_field(name="👤 Joueur 2", value="🕓 En attente...", inline=True)
            new_embed.add_field(name="Status", value="🕓 En attente d'un second joueur.", inline=False)
            new_embed.set_footer(text="Cliquez sur le bouton pour rejoindre le duel.")
            
            new_view = RejoindreView(message_id=message_initial.id, joueur1=joueur1, montant=montant)
            
            duels[message_initial.id] = new_view.duel_data

            role_membre = discord.utils.get(interaction.guild.roles, name="membre")
            contenu_ping = f"{role_membre.mention} — Un nouveau duel est prêt ! Un joueur est attendu."
            
            await message_initial.edit(content=contenu_ping, embed=new_embed, view=new_view, allowed_mentions=discord.AllowedMentions(roles=True))
            await interaction.response.send_message("✅ Tu as quitté le duel. Le créateur attend maintenant un autre joueur.", ephemeral=True)
        except Exception as e:
            print(f"Erreur lors de l'annulation du duel par joueur2: {e}")
            await interaction.response.send_message("❌ Une erreur s'est produite lors de l'annulation du duel.", ephemeral=True)


@bot.tree.command(name="statsall", description="Affiche les stats de morpion à vie.")
async def statsall(interaction: discord.Interaction):
    if not isinstance(interaction.channel, discord.TextChannel) or interaction.channel.name != "morpion":
        await interaction.response.send_message("❌ Cette commande ne peut être utilisée que dans le salon #morpion.", ephemeral=True)
        return

    c.execute("""
    SELECT joueur_id,
           SUM(CASE WHEN gagnant_id = joueur_id THEN 1 ELSE 0 END) as victoires,
           SUM(CASE WHEN est_nul = 1 THEN 1 ELSE 0 END) as nuls,
           SUM(CASE WHEN gagnant_id != joueur_id AND est_nul = 0 THEN 1 ELSE 0 END) as defaites,
           COUNT(*) as total_parties
    FROM (
        SELECT joueur1_id as joueur_id, gagnant_id, est_nul FROM parties
        UNION ALL
        SELECT joueur2_id as joueur_id, gagnant_id, est_nul FROM parties
    )
    GROUP BY joueur_id
    """)
    data = c.fetchall()

    stats = []
    for user_id, victoires, nuls, defaites, total_parties in data:
        stats.append((user_id, victoires, nuls, defaites, total_parties))

    stats.sort(key=lambda x: x[1], reverse=True)

    if not stats:
        await interaction.response.send_message("Aucune donnée statistique disponible.", ephemeral=True)
        return

    view = StatsView(interaction, stats)
    await interaction.response.send_message(embed=view.get_embed(), view=view, ephemeral=False)

@bot.tree.command(name="mystats", description="Affiche tes statistiques de morpion personnelles.")
async def mystats(interaction: discord.Interaction):
    user_id = interaction.user.id

    c.execute("""
    SELECT joueur_id,
           SUM(CASE WHEN gagnant_id = joueur_id THEN 1 ELSE 0 END) as victoires,
           SUM(CASE WHEN est_nul = 1 THEN 1 ELSE 0 END) as nuls,
           SUM(CASE WHEN gagnant_id != joueur_id AND est_nul = 0 THEN 1 ELSE 0 END) as defaites,
           COUNT(*) as total_parties
    FROM (
        SELECT joueur1_id as joueur_id, gagnant_id, est_nul FROM parties
        UNION ALL
        SELECT joueur2_id as joueur_id, gagnant_id, est_nul FROM parties
    )
    WHERE joueur_id = ?
    GROUP BY joueur_id
    """, (user_id,))
    
    stats_data = c.fetchone()

    if not stats_data:
        embed = discord.Embed(
            title="📊 Tes Statistiques Morpion",
            description="❌ Tu n'as pas encore participé à un duel. Joue ton premier duel pour voir tes stats !",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    _, victoires, nuls, defaites, total_parties = stats_data
    winrate = (victoires / total_parties * 100) if total_parties > 0 else 0.0

    embed = discord.Embed(
        title=f"📊 Statistiques de {interaction.user.display_name}",
        description="Voici un résumé de tes performances au morpion.",
        color=discord.Color.gold()
    )

    embed.add_field(name="Duels joués", value=f"**{total_parties}**", inline=False)
    embed.add_field(name="Victoires", value=f"**{victoires}**", inline=True)
    embed.add_field(name="Nuls", value=f"**{nuls}**", inline=True)
    embed.add_field(name="Défaites", value=f"**{defaites}**", inline=True)
    embed.add_field(name="Taux de victoire", value=f"**{winrate:.1f}%**", inline=False)

    embed.set_thumbnail(url=interaction.user.avatar.url if interaction.user.avatar else None)
    embed.set_footer(text="Bonne chance pour tes prochains duels !")

    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- Démarrage du bot ---
@bot.event
async def on_ready():
    print(f"{bot.user} est prêt !")
    try:
        await bot.tree.sync()
        print("✅ Commandes synchronisées.")
    except Exception as e:
        print(f"Erreur : {e}")

keep_alive()
bot.run(token)
