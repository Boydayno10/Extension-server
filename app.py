import os
from flask import Flask, jsonify, request

from translation_pipeline import translate

app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/translate", methods=["POST"])
def translate_route():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    direction = data.get("direction", "auto")  # "pt_to_em", "em_to_pt" ou "auto"

    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "Campo 'text' é obrigatório"}), 400

    if direction not in {"auto", "pt_to_em", "em_to_pt"}:
        return jsonify({"error": "direction inválido"}), 400

    try:
        output = translate(text, direction=direction)
        return jsonify({"text": text, "direction": direction, "translation": output})
    except Exception as exc:  # erro de Supabase ou pipeline
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    # Certifique-se de que SUPABASE_URL e SUPABASE_*_KEY estejam definidos
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
