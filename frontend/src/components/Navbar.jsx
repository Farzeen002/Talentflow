// import { useNavigate } from "react-router-dom";

// const Navbar = () => {
//   const navigate = useNavigate();

//   const handleLogout = () => {
//     localStorage.removeItem("token");
//     navigate("/login");
//   };

//   return (
//     <div className="bg-white shadow-sm px-6 py-4 flex justify-between items-center">
      
//       <h1
//         className="text-xl font-bold cursor-pointer"
//         onClick={() => navigate("/dashboard")}
//       >
//         Recruitment System
//       </h1>

//       <div className="flex items-center gap-4">
//         <button
//           onClick={() => navigate("/dashboard")}
//           className="text-sm text-slate-700 hover:text-blue-600 hover:bg-slate-100 px-3 py-2 rounded-lg transition"
//         >
//           Dashboard
//         </button>
//         <button
//           onClick={() => navigate("/jobs/create")}
//           className="text-sm text-slate-700 hover:text-blue-600 hover:bg-slate-100 px-3 py-2 rounded-lg transition"
//         >
//           Jobs
//         </button>
//         <button
//           onClick={() => navigate("/candidates")}
//           className="text-sm text-slate-700 hover:text-blue-600 hover:bg-slate-100 px-3 py-2 rounded-lg transition"
//         >
//           Candidates
//         </button>

//         <button
//           onClick={handleLogout}
//           className="bg-red-500 text-white px-4 py-2 rounded-lg hover:bg-red-600 transition"
//         >
//           Logout
//         </button>
//       </div>
//     </div>
//   );
// };

// export default Navbar;