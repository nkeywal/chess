class TablebaseAPI {
    static getBaseUrl() {
        return 'https://tablebase.lichess.ovh/standard';
    }

    static async fetchResult(fen) {
        // Handle spaces by encoding
        const encodedFen = encodeURIComponent(fen);
        const url = `${this.getBaseUrl()}?fen=${encodedFen}`;

        try {
            const response = await fetch(url);
            if (!response.ok) {
                const errText = await response.text();
                throw new Error(`API Error: ${response.status} ${response.statusText} - ${errText}`);
            }
            const data = await response.json();
            
            // Normalize WDL if missing but category exists
            if (typeof data.wdl === 'undefined' && data.category) {
                if (data.category === 'win') data.wdl = 2;
                else if (data.category === 'loss') data.wdl = -2;
                else if (data.category === 'draw') data.wdl = 0;
                else if (data.category === 'cursed-win') data.wdl = 1; // 50-move rule win but potentially draw
                else if (data.category === 'blessed-loss') data.wdl = -1; // 50-move rule loss but potentially draw
                else data.wdl = 0; // Default or unknown
            }
            
            return data;
        } catch (error) {
            console.error("Tablebase fetch error:", error);
            throw error;
        }
    }
}
// Expose to global scope
window.TablebaseAPI = TablebaseAPI;