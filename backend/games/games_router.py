from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ranking import glicko
from ranking.outcome import Outcome
from ranking.rating import Rating
from utility import sqlite_db

router = APIRouter(prefix='/games')


@router.get("/")
async def get_games():
    with sqlite_db.connection() as con:
        game_result = con.execute("SELECT game_id, draw, winner_id, loser_id, winner_rating, loser_rating, winner_rating_deviation, loser_rating_deviation FROM games")
        game_rows = game_result.fetchall()
        if game_rows is None:
            raise HTTPException(status_code=404, detail="Games not found")

        players_result = con.execute("SELECT player_id, username FROM players")
        player_rows = players_result.fetchall()
        if player_rows is None:
            raise HTTPException(status_code=404, detail="Players not found")

        players = {row[0]: row[1] for row in player_rows}

        def row_to_response(row):
            return {
                "game_id": row[0],
                "draw": row[1],
                "winner_id": row[2],
                "winner_username": players[row[2]],
                "loser_id": row[3],
                "loser_username": players[row[3]],
                "winner_rating": row[4],
                "loser_rating": row[5],
                "winner_rating_deviation": row[6],
                "loser_rating_deviation": row[7]
            }

        return [row_to_response(row) for row in game_rows]


class SubmitGameRequest(BaseModel):
    winner_id: int
    loser_id: int
    draw: bool = False


@router.post("/submit")
async def submit_game(submit_game_request: SubmitGameRequest):
    with sqlite_db.connection() as con:
        # Get the winner_rating, loser_rating, winner_rating_deviation, loser_rating_deviation from the players table for both players
        winner_result = con.execute("SELECT current_rating, current_rating_deviation FROM players WHERE player_id = ?", [submit_game_request.winner_id])
        loser_result = con.execute("SELECT current_rating, current_rating_deviation FROM players WHERE player_id = ?", [submit_game_request.loser_id])
        winner_rating, winner_rating_deviation = winner_result.fetchone()
        loser_rating, loser_rating_deviation = loser_result.fetchone()

        # Make sure the winner and loser exist
        if winner_rating is None or winner_rating_deviation is None:
            raise HTTPException(status_code=404, detail="Player with that winner id doesn't exist")
        if loser_rating is None or loser_rating_deviation is None:
            raise HTTPException(status_code=404, detail="Player with that loser id doesn't exist")

        # Update the winner and loser ratings using glicko
        new_winner_rating = glicko.Calculator().score_games(
            Rating(winner_rating, winner_rating_deviation),
            [(Outcome.WIN, Rating(loser_rating, loser_rating_deviation))])
        new_loser_rating = glicko.Calculator().score_games(
            Rating(loser_rating, loser_rating_deviation),
            [(Outcome.LOSS, Rating(winner_rating, winner_rating_deviation))])

        # Update the winner and loser ratings in the players table
        con.execute("UPDATE players SET current_rating = ?, current_rating_deviation = ? WHERE player_id = ?", [new_winner_rating.value, new_winner_rating.deviation, submit_game_request.winner_id])
        con.execute("UPDATE players SET current_rating = ?, current_rating_deviation = ? WHERE player_id = ?", [new_loser_rating.value, new_loser_rating.deviation, submit_game_request.loser_id])

        # Add the rating changes into the players_rating_history table
        con.execute(
            "INSERT INTO players_rating_history (player_id, rating, rating_deviation) VALUES (?, ?, ?)",
            [submit_game_request.winner_id, new_winner_rating.value, new_winner_rating.deviation])
        con.execute(
            "INSERT INTO players_rating_history (player_id, rating, rating_deviation) VALUES (?, ?, ?)",
            [submit_game_request.loser_id, new_loser_rating.value, new_loser_rating.deviation])

        # Get id of inserted game
        game_insert_result = con.execute(
            "INSERT INTO games (draw, winner_id, loser_id, winner_rating, loser_rating, winner_rating_deviation, loser_rating_deviation) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [submit_game_request.draw, submit_game_request.winner_id, submit_game_request.loser_id, winner_rating, loser_rating, winner_rating_deviation, loser_rating_deviation])

        game_id = game_insert_result.lastrowid

        return {
            "old_winner_rating": winner_rating,
            "old_loser_rating": loser_rating,
            "new_winner_rating": new_winner_rating,
            "new_loser_rating": new_loser_rating,
            "game_id": game_id
        }
