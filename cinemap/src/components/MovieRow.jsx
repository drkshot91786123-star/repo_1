import { useRef } from 'react'
import MovieCard from './MovieCard.jsx'
import './MovieRow.css'

const SCROLL_AMOUNT = 600

export default function MovieRow({ title, movies, genres, onSelect }) {
  const trackRef = useRef(null)

  const scroll = (dir) => {
    if (!trackRef.current) return
    trackRef.current.scrollBy({ left: dir * SCROLL_AMOUNT, behavior: 'smooth' })
  }

  if (!movies || movies.length === 0) return null

  return (
    <section className="movie-row">
      <div className="movie-row-header">
        <div className="movie-row-accent" />
        <h2 className="movie-row-title">{title}</h2>
      </div>
      <div className="movie-row-track-wrapper">
        <button className="movie-row-arrow left" onClick={() => scroll(-1)} aria-label="Scroll left">‹</button>
        <div className="movie-row-track" ref={trackRef}>
          {movies.map(movie => (
            <MovieCard key={movie.id} movie={movie} genres={genres} onSelect={onSelect} />
          ))}
        </div>
        <button className="movie-row-arrow right" onClick={() => scroll(1)} aria-label="Scroll right">›</button>
      </div>
    </section>
  )
}
