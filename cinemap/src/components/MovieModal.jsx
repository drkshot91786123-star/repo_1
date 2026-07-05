import { useEffect } from 'react'
import './MovieModal.css'

const POSTER_BASE = 'https://image.tmdb.org/t/p/w500'
const BACKDROP_BASE = 'https://image.tmdb.org/t/p/original'

export default function MovieModal({ movie, genres, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  }, [onClose])

  const year = movie.release_date?.slice(0, 4) ?? '—'
  const runtime = movie.runtime
    ? `${Math.floor(movie.runtime / 60)}h ${movie.runtime % 60}m`
    : null
  const movieGenres = (movie.genre_ids ?? []).map(id => genres[id]).filter(Boolean)

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose} aria-label="Close">✕</button>

        {movie.backdrop_path ? (
          <img
            className="modal-backdrop-img"
            src={`${BACKDROP_BASE}${movie.backdrop_path}`}
            alt=""
          />
        ) : (
          <div className="modal-backdrop-placeholder" />
        )}

        <div className="modal-body">
          <div className="modal-poster">
            {movie.poster_path && (
              <img src={`${POSTER_BASE}${movie.poster_path}`} alt={movie.title} />
            )}
          </div>

          <div className="modal-info">
            <h2 className="modal-title">{movie.title}</h2>
            {movie.tagline && <p className="modal-tagline">"{movie.tagline}"</p>}

            <div className="modal-meta">
              <span className="modal-rating">★ {movie.vote_average.toFixed(1)}</span>
              <span className="modal-year">{year}</span>
              {runtime && <span className="modal-runtime">{runtime}</span>}
            </div>

            {movieGenres.length > 0 && (
              <div className="modal-genres">
                {movieGenres.map(g => (
                  <span key={g} className="modal-genre-tag">{g}</span>
                ))}
              </div>
            )}

            <p className="modal-overview">{movie.overview}</p>

            <button className="modal-play-btn">▶ Play</button>
          </div>
        </div>
      </div>
    </div>
  )
}
